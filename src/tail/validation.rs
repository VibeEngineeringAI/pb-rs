//! Structural checks on IPC record-batch metadata before arrow-ipc decodes it.
//!
//! arrow-ipc (pinned to 60.0.0) validates most array contents after it has
//! built `ArrayData`: offsets, value buffer sizes, child lengths, dictionary
//! keys and declared null counts all come back as errors. A few pieces of
//! metadata are trusted earlier, by constructors that assert:
//!
//! - a validity bitmap is wrapped in `BooleanBuffer::new(buffer, 0, len)`
//!   whenever a node declares nulls, which asserts the bitmap holds `len` bits;
//! - union type IDs and dense-union offsets are sliced to `len` and `4 * len`
//!   bytes, and the offsets are reinterpreted as `i32`, which asserts alignment;
//! - signed node and batch lengths are converted with `as usize`, so a
//!   negative length becomes a huge one (a Null column of length -1 is
//!   accepted and then rendered row by row without end);
//! - a compressed buffer's declared uncompressed size is allocated up front.
//!
//! This module walks the batch's nodes and buffers in the order arrow-ipc's
//! `create_array` consumes them and rejects exactly that metadata, so hostile
//! input fails as an ordinary decode error instead of unwinding or aborting the
//! worker. It never makes data valid that arrow would reject, and it leaves
//! every other check to arrow. If metadata runs out (too few nodes or
//! buffers), the walk stops and arrow reports the mismatch itself.
//!
//! These rejections are safety divergences: Go's reader trusts the same
//! metadata and may panic, misread memory or render garbage instead.

use arrow_ipc::{CompressionType, MetadataVersion};
use arrow_schema::{DataType, Fields, UnionMode};

/// Format bounds on decompressed size per compressed byte. A declared size
/// above these can never be produced, so arrow would fail its length check
/// only after allocating it. LZ4 sequences expand at most ~255x (one length
/// byte adds 255 bytes); a 4-byte zstd RLE block yields at most 128 KiB
/// (32768x). Both bounds carry a 2x margin, so they reject only impossible
/// sizes. Sizes within them are still allocated up front: the memory limit
/// for a valid-looking hostile size is the host allocator's (unknown).
const LZ4_MAX_EXPANSION: u64 = 512;
const ZSTD_MAX_EXPANSION: u64 = 65_536;

/// Go's reading of the schema message, for the cases where arrow-rs would
/// accept a schema Go rejects, panic converting it, or report it differently.
///
/// Go converts each field's children before the field's own type, wrapping a
/// returned error with the path ("could not convert field N from flatbuf",
/// "could not convert field child N", "could not convert field type"). A
/// panic while building a type skips that wrapping and is recovered by the
/// reader as "unknown error while reading". This walk follows the same order
/// and returns Go's message for the first problem; the caller adds the
/// Flight reader prefix. It checks child counts and Map/run-end shapes,
/// integer, floating-point and decimal widths, union type IDs, FixedSizeList
/// sizes, dictionary index types and type kinds Go does not implement. Time,
/// date, interval and duration units are left to arrow, which also rejects
/// them (with different text).
pub(super) fn ipc_schema(schema: &arrow_ipc::Schema<'_>) -> Result<(), String> {
    let mut walk = GoSchema::default();
    for (index, field) in schema.fields().into_iter().flatten().enumerate() {
        match walk.field(field) {
            Ok(_) => {}
            Err(Fault::Panic(message)) => {
                return Err(format!("arrow/ipc: unknown error while reading: {message}"));
            }
            Err(Fault::Error(message)) => {
                return Err(format!(
                    "arrow/ipc: could not decode schema from message schema: \
                     arrow/ipc: could not convert field {index} from flatbuf: {message}"
                ));
            }
        }
    }
    Ok(())
}

enum Fault {
    /// A returned error, wrapped with field context as it propagates.
    Error(String),
    /// A recovered panic, never wrapped.
    Panic(String),
}

impl Fault {
    fn context(self, prefix: impl std::fmt::Display) -> Self {
        match self {
            Fault::Error(message) => Fault::Error(format!("{prefix}{message}")),
            panic => panic,
        }
    }
}

/// Go's field as converted from the schema: what its type comparisons see.
#[derive(Clone, Debug, PartialEq)]
struct GoField {
    name: String,
    nullable: bool,
    metadata: Vec<(String, String)>,
    data_type: GoType,
}

/// Go's data type, keeping every property its `TypeEqual` or (for the types
/// it compares with `reflect.DeepEqual`) its struct equality can observe.
/// Derived equality is that deep equality.
#[derive(Clone, Debug, PartialEq)]
enum GoType {
    /// A type without children, identified by its format tag and parameters.
    Leaf(arrow_ipc::Type, Vec<i64>, Option<String>),
    List(Box<GoField>),
    LargeList(Box<GoField>),
    FixedSizeList(i32, Box<GoField>),
    /// Go rebuilds map fields itself: only the key/item types, the item's
    /// nullability and key sorting survive.
    Map {
        key: Box<GoType>,
        item: Box<GoType>,
        item_nullable: bool,
        sorted: bool,
    },
    Struct(Vec<GoField>),
    Union {
        mode: i16,
        codes: Vec<i64>,
        fields: Vec<GoField>,
    },
    RunEndEncoded(Box<GoType>, Box<GoType>),
    Dictionary {
        index: (i32, bool),
        values: Box<GoType>,
        ordered: bool,
    },
}

/// Go's `arrow.TypeEqual` without metadata checks. Lists compare element
/// types and nullability but not element names; structs and unions compare
/// names, nullability and types; maps compare key/item types and
/// nullability; run-end types compare both children. Everything else,
/// including large lists and dictionaries, uses deep equality.
fn type_equal(left: &GoType, right: &GoType) -> bool {
    use GoType::*;
    let fields_equal = |l: &[GoField], r: &[GoField]| {
        l.len() == r.len()
            && l.iter().zip(r).all(|(l, r)| {
                l.name == r.name
                    && l.nullable == r.nullable
                    && type_equal(&l.data_type, &r.data_type)
            })
    };
    match (left, right) {
        (List(l), List(r)) => type_equal(&l.data_type, &r.data_type) && l.nullable == r.nullable,
        (FixedSizeList(n, l), FixedSizeList(m, r)) => {
            type_equal(&l.data_type, &r.data_type) && n == m && l.nullable == r.nullable
        }
        (
            Map {
                key,
                item,
                item_nullable,
                ..
            },
            Map {
                key: other_key,
                item: other_item,
                item_nullable: other_nullable,
                ..
            },
        ) => {
            type_equal(key, other_key)
                && type_equal(item, other_item)
                && item_nullable == other_nullable
        }
        (Struct(l), Struct(r)) => fields_equal(l, r),
        (
            Union {
                mode,
                codes,
                fields,
            },
            Union {
                mode: other_mode,
                codes: other_codes,
                fields: other_fields,
            },
        ) => mode == other_mode && codes == other_codes && fields_equal(fields, other_fields),
        (RunEndEncoded(ends, values), RunEndEncoded(other_ends, other_values)) => {
            type_equal(values, other_values) && type_equal(ends, other_ends)
        }
        (left, right) => left == right,
    }
}

/// Dictionary value types registered so far, as Go's dictionary memo holds
/// them: a later field with the same ID must have an equal value type.
#[derive(Default)]
struct GoSchema {
    dictionaries: std::collections::HashMap<i64, GoType>,
}

impl GoSchema {
    fn field(&mut self, field: arrow_ipc::Field<'_>) -> Result<GoField, Fault> {
        let mut children = Vec::new();
        for (index, child) in field.children().into_iter().flatten().enumerate() {
            children.push(self.field(child).map_err(|fault| {
                fault.context(format_args!(
                    "arrow/ipc: could not convert field child {index}: "
                ))
            })?);
        }
        let data_type = self
            .data_type(field, children)
            .map_err(|fault| fault.context("arrow/ipc: could not convert field type: "))?;
        let metadata = field
            .custom_metadata()
            .into_iter()
            .flatten()
            .map(|kv| {
                (
                    kv.key().unwrap_or_default().to_owned(),
                    kv.value().unwrap_or_default().to_owned(),
                )
            })
            .collect();
        Ok(GoField {
            name: field.name().unwrap_or_default().to_owned(),
            nullable: field.nullable(),
            metadata,
            data_type,
        })
    }

    fn data_type(
        &mut self,
        field: arrow_ipc::Field<'_>,
        children: Vec<GoField>,
    ) -> Result<GoType, Fault> {
        let concrete = go_type(field, children)?;
        let Some(dictionary) = field.dictionary() else {
            return Ok(concrete);
        };
        // The index type is read after the value type, then the value type
        // is registered under the dictionary ID.
        let index = dictionary.indexType();
        let width = index.map_or(0, |int| int.bitWidth());
        go_int(width).map_err(Fault::Error)?;
        let id = dictionary.id();
        match self.dictionaries.get(&id) {
            Some(existing) if !type_equal(existing, &concrete) => {
                return Err(Fault::Error(format!(
                    "arrow/ipc: conflicting dictionary types for id {id}"
                )));
            }
            _ => {
                self.dictionaries.insert(id, concrete.clone());
            }
        }
        Ok(GoType::Dictionary {
            index: (width, index.is_some_and(|int| int.is_signed())),
            values: Box::new(concrete),
            ordered: dictionary.isOrdered(),
        })
    }
}

fn go_type(field: arrow_ipc::Field<'_>, children: Vec<GoField>) -> Result<GoType, Fault> {
    use arrow_ipc::{Precision, Type};
    fn error<T>(message: String) -> Result<T, Fault> {
        Err(Fault::Error(message))
    }
    if field.type_().is_none() {
        return error("arrow/ipc: could not load field type data".into());
    }
    let count = children.len();
    let only_child = |kind: &str, mut children: Vec<GoField>| {
        if children.len() == 1 {
            Ok(Box::new(children.remove(0)))
        } else {
            error(format!(
                "arrow/ipc: {kind} must have exactly 1 child field (got={count})"
            ))
        }
    };
    let leaf = |parameters: Vec<i64>| Ok(GoType::Leaf(field.type_type(), parameters, None));
    match field.type_type() {
        Type::NONE => error("arrow/ipc: Type metadata cannot be none".into()),
        Type::Int => {
            let int = field.type_as_int();
            let width = int.map_or(0, |int| int.bitWidth());
            go_int(width).map_err(Fault::Error)?;
            leaf(vec![
                i64::from(width),
                i64::from(int.is_some_and(|int| int.is_signed())),
            ])
        }
        Type::FloatingPoint => {
            let precision = field
                .type_as_floating_point()
                .map_or(Precision(0), |float| float.precision());
            if !matches!(
                precision,
                Precision::HALF | Precision::SINGLE | Precision::DOUBLE
            ) {
                return error(format!(
                    "arrow/ipc: floating point type with {} precision not implemented",
                    precision.0
                ));
            }
            leaf(vec![i64::from(precision.0)])
        }
        Type::Decimal => {
            let decimal = field.type_as_decimal();
            let width = decimal.map_or(0, |decimal| decimal.bitWidth());
            if !matches!(width, 128 | 256) {
                return error(format!("arrow/ipc: invalid decimal bitwidth: {width}"));
            }
            let (precision, scale) = decimal.map_or((0, 0), |d| (d.precision(), d.scale()));
            leaf(vec![
                i64::from(width),
                i64::from(precision),
                i64::from(scale),
            ])
        }
        Type::List => Ok(GoType::List(only_child("List", children)?)),
        Type::LargeList => Ok(GoType::LargeList(only_child("LargeList", children)?)),
        Type::FixedSizeList => {
            let child = only_child("FixedSizeList", children)?;
            let size = field.type_as_fixed_size_list().map_or(0, |l| l.listSize());
            if size <= 0 {
                return Err(Fault::Panic("arrow: invalid size".into()));
            }
            Ok(GoType::FixedSizeList(size, child))
        }
        Type::Struct_ => Ok(GoType::Struct(children)),
        Type::Union => {
            let union = field.type_as_union();
            let ids: Option<Vec<i32>> = union
                .and_then(|u| u.typeIds())
                .map(|ids| ids.iter().collect());
            let mode = union.map_or(arrow_ipc::UnionMode(0), |u| u.mode());
            let codes = go_union(ids.as_deref(), mode, count)?;
            Ok(GoType::Union {
                mode: mode.0,
                codes,
                fields: children,
            })
        }
        Type::Map => {
            if count != 1 {
                return error("arrow/ipc: Map must have exactly 1 child field".into());
            }
            let entries = &children[0];
            let pair = match &entries.data_type {
                GoType::Struct(pair) if !entries.nullable && pair.len() == 2 => pair,
                _ => {
                    return error(
                        "arrow/ipc: Map's key-item pairs must be non-nullable structs".into(),
                    );
                }
            };
            if pair[0].nullable {
                return error("arrow/ipc: Map's keys must be non-nullable".into());
            }
            Ok(GoType::Map {
                key: Box::new(pair[0].data_type.clone()),
                item: Box::new(pair[1].data_type.clone()),
                item_nullable: pair[1].nullable,
                sorted: field.type_as_map().is_some_and(|m| m.keysSorted()),
            })
        }
        Type::RunEndEncoded => {
            if count != 2 {
                return error(
                    "invalid: arrow/ipc: RunEndEncoded must have exactly 2 child fields".into(),
                );
            }
            let signed = matches!(
                &children[0].data_type,
                GoType::Leaf(Type::Int, parameters, _)
                    if parameters[1] == 1 && matches!(parameters[0], 16 | 32 | 64)
            );
            if !signed {
                return error(
                    "invalid: arrow/ipc: run-end encoded run_ends field must be one of \
                     int16, int32, or int64 type"
                        .into(),
                );
            }
            let mut children = children.into_iter().map(|child| Box::new(child.data_type));
            let ends = children.next().expect("two children");
            Ok(GoType::RunEndEncoded(
                ends,
                children.next().expect("two children"),
            ))
        }
        Type::FixedSizeBinary => leaf(vec![i64::from(
            field
                .type_as_fixed_size_binary()
                .map_or(0, |b| b.byteWidth()),
        )]),
        Type::Time => {
            let time = field.type_as_time();
            leaf(vec![
                i64::from(time.map_or(0, |t| t.bitWidth())),
                i64::from(time.map_or(0, |t| t.unit().0)),
            ])
        }
        Type::Timestamp => {
            let timestamp = field.type_as_timestamp();
            Ok(GoType::Leaf(
                Type::Timestamp,
                vec![i64::from(timestamp.map_or(0, |t| t.unit().0))],
                Some(
                    timestamp
                        .and_then(|t| t.timezone())
                        .unwrap_or_default()
                        .to_owned(),
                ),
            ))
        }
        Type::Date => leaf(vec![i64::from(
            field.type_as_date().map_or(0, |d| d.unit().0),
        )]),
        Type::Interval => leaf(vec![i64::from(
            field.type_as_interval().map_or(0, |i| i.unit().0),
        )]),
        Type::Duration => leaf(vec![i64::from(
            field.type_as_duration().map_or(0, |d| d.unit().0),
        )]),
        Type::Null
        | Type::Binary
        | Type::Utf8
        | Type::LargeBinary
        | Type::LargeUtf8
        | Type::Bool => leaf(Vec::new()),
        // Types newer than Go v13's format enum have no name there.
        _ => Err(Fault::Panic("arrow/ipc: type  not implemented".into())),
    }
}

fn go_int(width: i32) -> Result<(), String> {
    if width > 64 {
        Err(format!(
            "arrow/ipc: integers with more than 64 bits not implemented (bits={width})"
        ))
    } else if width < 8 {
        Err(format!(
            "arrow/ipc: integers with less than 8 bits not implemented (bits={width})"
        ))
    } else if !matches!(width, 8 | 16 | 32 | 64) {
        Err("arrow/ipc: integers not in cstdint are not implemented".into())
    } else {
        Ok(())
    }
}

/// Explicit type IDs must fit Go's int8 type code (an error). An unknown
/// union mode then panics when Go builds the type, and the resulting codes,
/// implicit or explicit, must be one per child and within 0..=127 (panics).
/// arrow-rs would instead wrap explicit IDs through `as i8` and assert on more
/// than 128 implicit children. Go accepts an empty explicit ID list (implicit
/// codes) and duplicate IDs, which arrow rejects: a declared divergence.
fn go_union(
    ids: Option<&[i32]>,
    mode: arrow_ipc::UnionMode,
    children: usize,
) -> Result<Vec<i64>, Fault> {
    let codes: Vec<i64> = match ids {
        Some(ids) if !ids.is_empty() => {
            if ids.iter().any(|&id| i8::try_from(id).is_err()) {
                return Err(Fault::Error("union type id out of bounds".into()));
            }
            ids.iter().map(|&id| i64::from(id)).collect()
        }
        // Go assigns int8(i), which wraps from 128 children on.
        _ => (0..children).map(|i| i64::from(i as u8 as i8)).collect(),
    };
    if !matches!(
        mode,
        arrow_ipc::UnionMode::Sparse | arrow_ipc::UnionMode::Dense
    ) {
        return Err(Fault::Panic("arrow: invalid union mode".into()));
    }
    if codes.len() != children {
        return Err(Fault::Panic(
            "arrow: union types should have the same number of fields as type codes".into(),
        ));
    }
    if codes.iter().any(|code| !(0..=127).contains(code)) {
        return Err(Fault::Panic("arrow: union type code out of bounds".into()));
    }
    Ok(codes)
}

/// Go's array-loading nesting limit. Loading a record batch, or a dictionary
/// batch's values, panics ("nested type limit reached") when any array nests
/// more than this many child levels; the reader recovers it as a read error.
pub(super) const GO_MAX_NESTING: usize = 64;

/// Child levels Go loads for a record batch with these columns.
pub(super) fn nesting(fields: &Fields) -> usize {
    fields
        .iter()
        .map(|f| type_nesting(f.data_type()))
        .max()
        .unwrap_or(0)
}

/// Child levels Go loads for one array of this type. Each child of a list,
/// fixed-size list, map (its entries struct, then key and value), struct,
/// union or run-end array is one level. A dictionary array loads only its
/// indices; its values load separately, from depth zero, in dictionary
/// batches.
pub(super) fn type_nesting(data_type: &DataType) -> usize {
    use DataType::*;
    let below = |children: &mut dyn Iterator<Item = &DataType>| {
        children
            .map(|child| 1 + type_nesting(child))
            .max()
            .unwrap_or(0)
    };
    match data_type {
        List(child) | LargeList(child) | FixedSizeList(child, _) | Map(child, _) => {
            1 + type_nesting(child.data_type())
        }
        Struct(children) => below(&mut children.iter().map(|f| f.data_type())),
        Union(children, _) => below(&mut children.iter().map(|(_, f)| f.data_type())),
        RunEndEncoded(ends, values) => {
            below(&mut [ends.data_type(), values.data_type()].into_iter())
        }
        _ => 0,
    }
}

/// Checks on the converted schema. arrow-data unwraps a negative
/// FixedSizeBinary width when it lays out the column. Go accepts that schema
/// and panics only when rendering a non-null value; rejecting it while
/// reading the schema is a declared divergence.
pub(super) fn schema_types(fields: &Fields) -> Result<(), String> {
    fn check(data_type: &DataType) -> Result<(), String> {
        use DataType::*;
        match data_type {
            FixedSizeBinary(width) if *width < 0 => {
                Err(format!("invalid FixedSizeBinary width {width}"))
            }
            Dictionary(_, values) => check(values),
            List(child)
            | LargeList(child)
            | ListView(child)
            | LargeListView(child)
            | FixedSizeList(child, _)
            | Map(child, _) => check(child.data_type()),
            RunEndEncoded(ends, values) => {
                check(ends.data_type())?;
                check(values.data_type())
            }
            Struct(children) => children.iter().try_for_each(|f| check(f.data_type())),
            Union(children, _) => children.iter().try_for_each(|(_, f)| check(f.data_type())),
            _ => Ok(()),
        }
    }
    fields.iter().try_for_each(|f| check(f.data_type()))
}

/// How a checked record batch should be decoded.
#[derive(Debug, PartialEq)]
pub(super) enum Layout {
    /// Decode with arrow-ipc.
    Arrays,
    /// A negative row count that the Go client reads without error and
    /// renders as a batch of no rows. Probed against Go arrow v13.0.0: when
    /// the batch and every top-level node share the negative length and each
    /// column is Null (null count equal to the length) or a field-less Struct
    /// (no nulls), `NumRows()` is negative, RecordToJSON writes nothing and
    /// tail prints only the batch separator. Columns with buffers whose node
    /// length is the same negative value fail in Go with a slice-bounds error
    /// instead, and remain rejected here.
    NoRows,
}

/// Check a record batch against the fields it will be decoded with.
///
/// Row counts differ for malformed producers. Go's record uses the batch
/// length as its row count (or, when that is negative, the first column's
/// length), requires every column's length to be at least that, and renders
/// only those rows. Arrow requires every top-level column length to equal the
/// batch length exactly. A longer column, or a negative batch length over
/// columns with buffers, therefore renders in Go and fails here: a declared
/// divergence, apart from the probed shape in [`Layout::NoRows`].
pub(super) fn record_batch(
    batch: &arrow_ipc::RecordBatch<'_>,
    fields: &Fields,
    body: &[u8],
    version: MetadataVersion,
) -> Result<Layout, String> {
    if batch.length() < 0 {
        return if rowless(batch, fields) {
            Ok(Layout::NoRows)
        } else {
            Err(format!("invalid record batch length {}", batch.length()))
        };
    }
    let mut walk = Walk::new(batch, body, version);
    for field in fields {
        match walk.field(field.data_type()) {
            Ok(()) => {}
            Err(Halt::Exhausted) => return Ok(Layout::Arrays),
            Err(Halt::Invalid(reason)) => return Err(reason),
        }
    }
    Ok(Layout::Arrays)
}

/// The characterized negative-length shape described on [`Layout::NoRows`].
/// Each such column consumes exactly one node.
fn rowless(batch: &arrow_ipc::RecordBatch<'_>, fields: &Fields) -> bool {
    let length = batch.length();
    let Some(nodes) = batch.nodes() else {
        return false;
    };
    nodes.len() >= fields.len()
        && fields.iter().zip(nodes.iter()).all(|(field, node)| {
            node.length() == length
                && match field.data_type() {
                    DataType::Null => node.null_count() == length,
                    DataType::Struct(children) => children.is_empty() && node.null_count() == 0,
                    _ => false,
                }
        })
}

/// A dictionary batch decodes as a single column of the dictionary's value
/// type (arrow-ipc's `get_dictionary_values`).
pub(super) fn dictionary(
    batch: &arrow_ipc::RecordBatch<'_>,
    values: &DataType,
    body: &[u8],
    version: MetadataVersion,
) -> Result<(), String> {
    // Negative dictionary lengths are not characterized; reject them all.
    if batch.length() < 0 {
        return Err(format!(
            "invalid dictionary batch length {}",
            batch.length()
        ));
    }
    record_batch(
        batch,
        &Fields::from(vec![arrow_schema::Field::new("", values.clone(), true)]),
        body,
        version,
    )
    .map(|_| ())
}

enum Halt {
    /// Metadata ended early; arrow reports the mismatch without panicking.
    Exhausted,
    Invalid(String),
}

struct Node {
    length: i64,
    null_count: i64,
}

struct Walk<'a> {
    nodes: std::vec::IntoIter<Node>,
    buffers: std::vec::IntoIter<(i64, i64)>,
    body: &'a [u8],
    codec: Option<CompressionType>,
    version: MetadataVersion,
}

impl<'a> Walk<'a> {
    fn new(batch: &arrow_ipc::RecordBatch<'_>, body: &'a [u8], version: MetadataVersion) -> Self {
        let nodes = batch
            .nodes()
            .map(|nodes| {
                nodes
                    .iter()
                    .map(|node| Node {
                        length: node.length(),
                        null_count: node.null_count(),
                    })
                    .collect()
            })
            .unwrap_or_default();
        let buffers: Vec<_> = batch
            .buffers()
            .map(|buffers| buffers.iter().map(|b| (b.offset(), b.length())).collect())
            .unwrap_or_default();
        Self {
            nodes: Vec::into_iter(nodes),
            buffers: buffers.into_iter(),
            body,
            codec: batch.compression().map(|c| c.codec()),
            version,
        }
    }

    /// Mirrors arrow-ipc 60 `RecordBatchDecoder::create_array`.
    fn field(&mut self, data_type: &DataType) -> Result<(), Halt> {
        use DataType::*;
        match data_type {
            Utf8 | Binary | LargeUtf8 | LargeBinary => {
                let node = self.node(true)?;
                self.validity(&node)?;
                self.buffer()?;
                self.buffer()?;
            }
            // View types read a per-column variadic buffer count. The renderer
            // rejects them at schema time (Go v13 has no view types), so they
            // never reach decoding; stop and let arrow handle the remainder.
            Utf8View | BinaryView => return Err(Halt::Exhausted),
            List(child) | LargeList(child) | Map(child, _) => {
                let node = self.node(true)?;
                self.validity(&node)?;
                self.buffer()?;
                self.field(child.data_type())?;
            }
            ListView(child) | LargeListView(child) => {
                let node = self.node(true)?;
                self.validity(&node)?;
                self.buffer()?;
                self.buffer()?;
                self.field(child.data_type())?;
            }
            FixedSizeList(child, size) => {
                let node = self.node(true)?;
                // Arrow multiplies the node length by the list size with an
                // asserting overflow check, so an overflowing length is
                // rejected here first. Go keeps the node length as the array
                // length and only requires it to cover the record's rows (see
                // `record_batch`), so it renders this; the rejection is a
                // declared safety divergence.
                if let Ok(size) = usize::try_from(*size)
                    && (node.length as usize).checked_mul(size).is_none()
                {
                    return Err(Halt::Invalid(format!(
                        "fixed-size list length {} overflows its {size} values",
                        node.length
                    )));
                }
                self.validity(&node)?;
                self.field(child.data_type())?;
            }
            Struct(children) => {
                let node = self.node(true)?;
                self.validity(&node)?;
                for child in children {
                    self.field(child.data_type())?;
                }
            }
            RunEndEncoded(run_ends, values) => {
                self.node(true)?;
                self.field(run_ends.data_type())?;
                self.field(values.data_type())?;
            }
            // Dictionary values arrive in dictionary batches, so a dictionary
            // column consumes only its index node and buffers.
            Dictionary(_, _) => {
                let node = self.node(true)?;
                self.validity(&node)?;
                self.buffer()?;
            }
            Union(children, mode) => {
                // Arrow ignores a union node's null count; only its length is used.
                let node = self.node(false)?;
                let length = node.length as u64;
                if self.version < MetadataVersion::V5 {
                    self.buffer()?;
                }
                if self.buffer()?.size < length {
                    return Err(Halt::Invalid(format!(
                        "union type ids are shorter than {length} rows"
                    )));
                }
                if *mode == UnionMode::Dense {
                    let offsets = self.buffer()?;
                    let needed = length.checked_mul(4).ok_or_else(|| {
                        Halt::Invalid(format!("union length {length} overflows its offsets"))
                    })?;
                    if offsets.size < needed {
                        return Err(Halt::Invalid(format!(
                            "union offsets are shorter than {length} rows"
                        )));
                    }
                    if !offsets.aligned_i32 {
                        return Err(Halt::Invalid("union offsets are not 4-byte aligned".into()));
                    }
                }
                for (_, child) in children.iter() {
                    self.field(child.data_type())?;
                }
            }
            Null => {
                self.node(true)?;
            }
            // Primitive, boolean, temporal, decimal and fixed-size binary:
            // validity and one value buffer.
            _ => {
                let node = self.node(true)?;
                self.validity(&node)?;
                self.buffer()?;
            }
        }
        Ok(())
    }

    /// Next node with a non-negative length and, where arrow uses it, a null
    /// count within that length.
    fn node(&mut self, counts_nulls: bool) -> Result<Node, Halt> {
        let node = self.nodes.next().ok_or(Halt::Exhausted)?;
        if node.length < 0 {
            return Err(Halt::Invalid(format!(
                "invalid node length {}",
                node.length
            )));
        }
        if counts_nulls && !(0..=node.length).contains(&node.null_count) {
            return Err(Halt::Invalid(format!(
                "invalid null count {} for node length {}",
                node.null_count, node.length
            )));
        }
        Ok(node)
    }

    /// The validity buffer, which must hold one bit per row when nulls are
    /// declared (arrow ignores it otherwise).
    fn validity(&mut self, node: &Node) -> Result<(), Halt> {
        let buffer = self.buffer()?;
        let needed = (node.length as u64).div_ceil(8);
        if node.null_count > 0 && buffer.size < needed {
            return Err(Halt::Invalid(format!(
                "validity bitmap is shorter than {} rows",
                node.length
            )));
        }
        Ok(())
    }

    /// Next buffer's decoded size. Body ranges were already checked against
    /// the body length.
    fn buffer(&mut self) -> Result<Decoded, Halt> {
        let (offset, length) = self.buffers.next().ok_or(Halt::Exhausted)?;
        let (offset, length) = (offset as usize, length as usize);
        let Some(codec) = self.codec else {
            return Ok(Decoded {
                size: length as u64,
                aligned_i32: offset.is_multiple_of(4),
            });
        };
        if length == 0 {
            return Ok(Decoded {
                size: 0,
                aligned_i32: true,
            });
        }
        // A compressed buffer starts with its decoded size as an i64. A
        // shorter buffer has no prefix; Go reports the short read.
        if length < 8 {
            return Err(Halt::Invalid("unexpected EOF".into()));
        }
        let Some(prefix) = self.body.get(offset..offset + 8) else {
            return Err(Halt::Exhausted);
        };
        let declared = i64::from_le_bytes(prefix.try_into().expect("eight bytes"));
        let compressed = (length - 8) as u64;
        match declared {
            0 => Ok(Decoded {
                size: 0,
                aligned_i32: true,
            }),
            // Stored uncompressed after the prefix, at offset + 8.
            -1 => Ok(Decoded {
                size: compressed,
                aligned_i32: offset.is_multiple_of(4),
            }),
            i64::MIN..=-2 => Err(Halt::Exhausted),
            declared => {
                let factor = if codec == CompressionType::ZSTD {
                    ZSTD_MAX_EXPANSION
                } else {
                    LZ4_MAX_EXPANSION
                };
                let declared = declared as u64;
                if declared > compressed.saturating_mul(factor) {
                    return Err(Halt::Invalid(format!(
                        "compressed buffer declares {declared} bytes from {compressed}"
                    )));
                }
                // Decompression writes into a fresh allocation, which the
                // system allocator aligns for any primitive.
                Ok(Decoded {
                    size: declared,
                    aligned_i32: true,
                })
            }
        }
    }
}

struct Decoded {
    size: u64,
    /// Whether arrow can view this buffer as `i32` without realigning it.
    aligned_i32: bool,
}
