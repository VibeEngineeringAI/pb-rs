//! Arrow record batches rendered as the exact bytes of Go Arrow v13
//! `array.RecordToJSON`.
//!
//! Go's conversion is not `encoding/json`: Arrow v13's `internal/json` aliases
//! `github.com/goccy/go-json` v0.10.0. Each row first evaluates every column's
//! `GetOneForMarshal` in schema order. List-like values marshal their child
//! slice at that point, and goccy failures there panic. The row map is then
//! encoded, and goccy sorts keys by their *encoded* bytes. A float64 NaN/Inf at
//! that stage is an encoder error that stops the batch after earlier complete
//! rows. Float32 non-finite values are written as raw `NaN`/`+Inf`/`-Inf`
//! tokens. Those tokens fail only where a Marshaler's output is compacted,
//! which means inside list-like values, where Go panics. The two stages are
//! modelled as `gather` (GetOneForMarshal) and `encode` (goccy encoding). See
//! docs/phase6-values-notes.md for the source evidence and the open oracle items.
use arrow_array::ArrowPrimitiveType;
use arrow_array::cast::AsArray;
use arrow_array::types::*;
use arrow_array::{Array, BooleanArray, RecordBatch, UnionArray};
use arrow_buffer::{
    ArrowNativeType, IntervalDayTime, IntervalMonthDayNano, NullBuffer, ScalarBuffer, i256,
};

type F16 = <Float16Type as ArrowPrimitiveType>::Native;
use arrow_schema::{DataType, Field, Fields, IntervalUnit, SchemaRef, TimeUnit, UnionMode};
use std::fmt;

/// A schema-owned rendering plan: sorted, last-duplicate field order plus a
/// per-column encoder tree. It holds no batch borrows and is `Send`.
pub(super) struct BatchRenderer {
    row: ObjectPlan,
    row_slots: usize,
    /// List-marshal scratch, owned only by this renderer and reused across
    /// batches. After each batch it is released whenever the aggregate
    /// capacity exceeds `SCRATCH_RETAIN_BYTES`. Resource measurements must
    /// count it next to the session's output staging buffer; see
    /// `scratch_capacity`.
    scopes: Vec<Vec<Vec<u8>>>,
}

/// Aggregate list-scratch capacity retained between batches. One unusually
/// large nested batch cannot pin its high-water allocation.
const SCRATCH_RETAIN_BYTES: usize = 1 << 20;

/// An Arrow type that Go Arrow v13 IPC cannot decode, so Go fails during
/// reader initialization before any batch is rendered.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct UnsupportedType(pub String);
impl fmt::Display for UnsupportedType {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}
impl std::error::Error for UnsupportedType {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) enum RenderError {
    /// goccy returned an error for a row. Complete earlier rows remain in the
    /// output buffer, and Go ignores the error.
    Encode(String),
    /// Go panics inside `GetOneForMarshal` while building a row. Nothing from
    /// this batch is printed, and the output buffer is restored to its length
    /// on entry.
    Panic(String),
}
impl fmt::Display for RenderError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Encode(message) | Self::Panic(message) => f.write_str(message),
        }
    }
}
impl std::error::Error for RenderError {}

impl BatchRenderer {
    pub(super) fn new(schema: &SchemaRef) -> Result<Self, UnsupportedType> {
        let mut row_slots = 0;
        let row = ObjectPlan::new(schema.fields(), None, &mut row_slots)?;
        Ok(Self {
            row,
            row_slots,
            scopes: Vec::new(),
        })
    }

    /// `batch` may carry Binary/LargeBinary arrays wherever the plan's schema
    /// has Utf8/LargeUtf8, at any nesting depth; every other type must match.
    ///
    /// Append one JSON object plus LF per row, exactly as Go's
    /// `RecordToJSON` writes into its `bytes.Buffer`. The batch separator
    /// that `fmt.Println` adds is the caller's.
    pub(super) fn render(
        &mut self,
        batch: &RecordBatch,
        out: &mut Vec<u8>,
    ) -> Result<(), RenderError> {
        let entry = out.len();
        let columns: Vec<Col<'_>> = self
            .row
            .fields
            .iter()
            .zip(batch.columns())
            .map(|(plan, array)| Col::new(plan, array.as_ref()))
            .collect();
        if self.scopes.is_empty() {
            self.scopes.push(Vec::new());
        }
        self.scopes[0].resize_with(self.row_slots, Vec::new);
        let mut cx = Cx {
            scopes: std::mem::take(&mut self.scopes),
            depth: 0,
            raw: None,
        };
        let result = (|| {
            for row in 0..batch.num_rows() {
                let start = out.len();
                for column in &columns {
                    column.gather(row, &mut cx)?;
                }
                if let Err(fail) = encode_object(&self.row.order, &columns, row, out, &mut cx) {
                    out.truncate(start);
                    return Err(fail);
                }
                out.push(b'\n');
            }
            Ok(())
        })();
        self.scopes = cx.scopes;
        self.release_excess_scratch();
        match result {
            Ok(()) => Ok(()),
            Err(Fail::Encode(message)) => Err(RenderError::Encode(message)),
            Err(Fail::Panic(message)) => {
                out.truncate(entry);
                Err(RenderError::Panic(message))
            }
        }
    }
}

impl BatchRenderer {
    /// Bytes currently reserved by list-marshal scratch buffers, for
    /// resource observations.
    #[allow(dead_code)]
    pub(super) fn scratch_capacity(&self) -> usize {
        self.scopes.iter().flatten().map(Vec::capacity).sum()
    }

    fn release_excess_scratch(&mut self) {
        let retained: usize = self.scopes.iter().flatten().map(Vec::capacity).sum();
        // Diagnostics observe the batch peak before trimming, then what stays.
        #[cfg(feature = "diagnostics")]
        super::resources::renderer_scratch(retained);
        if retained > SCRATCH_RETAIN_BYTES {
            // Dropping the vectors returns their memory; the next nested
            // batch grows fresh buffers to its own size.
            self.scopes = Vec::new();
        } else {
            for slot in self.scopes.iter_mut().flatten() {
                slot.clear();
            }
        }
        #[cfg(feature = "diagnostics")]
        super::resources::renderer_scratch(self.scratch_capacity());
    }
}

/// Render a batch with a one-off plan. Long-lived subscriptions should keep a
/// `BatchRenderer` per schema instead.
#[allow(dead_code)]
pub(super) fn render_batch(batch: &RecordBatch, out: &mut Vec<u8>) -> Result<(), RenderError> {
    match BatchRenderer::new(&batch.schema()) {
        Ok(mut renderer) => renderer.render(batch, out),
        Err(err) => Err(RenderError::Encode(err.0)),
    }
}

enum Fail {
    Encode(String),
    Panic(String),
}

/// Raw non-finite float32 tokens written in the current marshal run.
#[derive(Clone, Copy)]
enum RawToken {
    NaN,
    PosInf,
    NegInf,
}

struct Cx {
    /// One slot vector per list nesting depth; slots hold list-like values
    /// marshalled during `gather` until the enclosing object is encoded.
    scopes: Vec<Vec<Vec<u8>>>,
    depth: usize,
    raw: Option<RawToken>,
}

struct ObjectPlan {
    fields: Vec<Plan>,
    /// Encoded key (with quotes) and field index, sorted as goccy sorts them.
    order: Vec<(Box<[u8]>, usize)>,
}

impl ObjectPlan {
    fn new(
        fields: &Fields,
        names: Option<[&str; 2]>,
        slots: &mut usize,
    ) -> Result<Self, UnsupportedType> {
        let plans = fields
            .iter()
            .map(|field| Plan::new(field, slots))
            .collect::<Result<Vec<_>, _>>()?;
        // Go assigns into a map, so the last column with a name wins.
        let mut order: Vec<(Vec<u8>, usize)> = Vec::with_capacity(fields.len());
        for (index, field) in fields.iter().enumerate() {
            let name = names.map_or(field.name().as_str(), |names| names[index.min(1)]);
            let mut key = Vec::with_capacity(name.len() + 3);
            write_str(&mut key, name.as_bytes());
            if let Some(existing) = order.iter_mut().find(|(k, _)| *k == key) {
                existing.1 = index;
            } else {
                order.push((key, index));
            }
        }
        // goccy compares the encoded key followed by the separator byte that
        // is later replaced with ':'; keys are unique so ',' only matters for
        // prefixes, and it sorts like the trailing comma it really is.
        order.sort_by(|(a, _), (b, _)| a.iter().chain(b",").cmp(b.iter().chain(b",")));
        Ok(Self {
            fields: plans,
            order: order
                .into_iter()
                .map(|(k, i)| (k.into_boxed_slice(), i))
                .collect(),
        })
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum ListKind {
    List,
    LargeList,
    FixedSizeList,
    Map,
}

#[derive(Clone, Copy)]
enum RunEnds {
    I16,
    I32,
    I64,
}

enum Plan {
    Null,
    Boolean,
    Int8,
    Int16,
    Int32,
    Int64,
    UInt8,
    UInt16,
    UInt32,
    UInt64,
    Float16,
    Float32,
    Float64,
    Utf8,
    LargeUtf8,
    Binary,
    LargeBinary,
    FixedSizeBinary,
    Date32,
    Date64,
    Time32(TimeUnit),
    Time64(TimeUnit),
    Timestamp(TimeUnit),
    Duration(TimeUnit),
    MonthInterval,
    DayTimeInterval,
    MonthDayNanoInterval,
    Decimal128 {
        precision: u8,
        scale: i8,
    },
    Decimal256 {
        precision: u8,
        scale: i8,
    },
    List {
        kind: ListKind,
        slot: usize,
        elem_slots: usize,
        elem: Box<Plan>,
    },
    Struct(ObjectPlan),
    Union {
        dense: bool,
        children: Vec<(i8, Plan)>,
    },
    Dictionary(Box<Plan>),
    RunEndEncoded {
        ends: RunEnds,
        values: Box<Plan>,
    },
}

fn unsupported(data_type: &DataType) -> UnsupportedType {
    UnsupportedType(format!(
        "Arrow type {data_type} is not supported by the Go Arrow v13 IPC reader"
    ))
}

impl Plan {
    fn new(field: &Field, slots: &mut usize) -> Result<Self, UnsupportedType> {
        Self::from_type(field.data_type(), slots)
    }

    fn from_type(data_type: &DataType, slots: &mut usize) -> Result<Self, UnsupportedType> {
        Ok(match data_type {
            DataType::Null => Self::Null,
            DataType::Boolean => Self::Boolean,
            DataType::Int8 => Self::Int8,
            DataType::Int16 => Self::Int16,
            DataType::Int32 => Self::Int32,
            DataType::Int64 => Self::Int64,
            DataType::UInt8 => Self::UInt8,
            DataType::UInt16 => Self::UInt16,
            DataType::UInt32 => Self::UInt32,
            DataType::UInt64 => Self::UInt64,
            DataType::Float16 => Self::Float16,
            DataType::Float32 => Self::Float32,
            DataType::Float64 => Self::Float64,
            DataType::Utf8 => Self::Utf8,
            DataType::LargeUtf8 => Self::LargeUtf8,
            DataType::Binary => Self::Binary,
            DataType::LargeBinary => Self::LargeBinary,
            DataType::FixedSizeBinary(_) => Self::FixedSizeBinary,
            DataType::Date32 => Self::Date32,
            DataType::Date64 => Self::Date64,
            DataType::Time32(unit) => Self::Time32(*unit),
            DataType::Time64(unit) => Self::Time64(*unit),
            // Go's GetOneForMarshal formats in UTC and ignores the zone.
            DataType::Timestamp(unit, _) => Self::Timestamp(*unit),
            DataType::Duration(unit) => Self::Duration(*unit),
            DataType::Interval(IntervalUnit::YearMonth) => Self::MonthInterval,
            DataType::Interval(IntervalUnit::DayTime) => Self::DayTimeInterval,
            DataType::Interval(IntervalUnit::MonthDayNano) => Self::MonthDayNanoInterval,
            DataType::Decimal128(precision, scale) => Self::Decimal128 {
                precision: *precision,
                scale: *scale,
            },
            DataType::Decimal256(precision, scale) => Self::Decimal256 {
                precision: *precision,
                scale: *scale,
            },
            DataType::List(child) => Self::list(ListKind::List, child, slots)?,
            DataType::LargeList(child) => Self::list(ListKind::LargeList, child, slots)?,
            DataType::FixedSizeList(child, _) => Self::list(ListKind::FixedSizeList, child, slots)?,
            DataType::Map(entries, _) => {
                let DataType::Struct(pair) = entries.data_type() else {
                    return Err(unsupported(data_type));
                };
                if entries.is_nullable() || pair.len() != 2 {
                    return Err(UnsupportedType(
                        "arrow/ipc: Map's key-item pairs must be non-nullable structs".into(),
                    ));
                }
                if pair[0].is_nullable() {
                    return Err(UnsupportedType(
                        "arrow/ipc: Map's keys must be non-nullable".into(),
                    ));
                }
                let slot = bump(slots);
                let mut elem_slots = 0;
                // Go's MapOf renames the pair fields to "key" and "value".
                let elem = Self::Struct(ObjectPlan::new(
                    pair,
                    Some(["key", "value"]),
                    &mut elem_slots,
                )?);
                Self::List {
                    kind: ListKind::Map,
                    slot,
                    elem_slots,
                    elem: Box::new(elem),
                }
            }
            DataType::Struct(fields) => Self::Struct(ObjectPlan::new(fields, None, slots)?),
            DataType::Union(fields, mode) => Self::Union {
                dense: *mode == UnionMode::Dense,
                children: fields
                    .iter()
                    .map(|(id, field)| Ok((id, Self::new(field, slots)?)))
                    .collect::<Result<_, UnsupportedType>>()?,
            },
            DataType::Dictionary(_, values) => {
                Self::Dictionary(Box::new(Self::from_type(values, slots)?))
            }
            DataType::RunEndEncoded(ends, values) => Self::RunEndEncoded {
                ends: match ends.data_type() {
                    DataType::Int16 => RunEnds::I16,
                    DataType::Int32 => RunEnds::I32,
                    DataType::Int64 => RunEnds::I64,
                    _ => return Err(unsupported(data_type)),
                },
                values: Box::new(Self::new(values, slots)?),
            },
            DataType::BinaryView
            | DataType::Utf8View
            | DataType::ListView(_)
            | DataType::LargeListView(_)
            | DataType::Decimal32(..)
            | DataType::Decimal64(..) => return Err(unsupported(data_type)),
        })
    }

    fn list(kind: ListKind, child: &Field, slots: &mut usize) -> Result<Self, UnsupportedType> {
        let slot = bump(slots);
        let mut elem_slots = 0;
        let elem = Self::new(child, &mut elem_slots)?;
        Ok(Self::List {
            kind,
            slot,
            elem_slots,
            elem: Box::new(elem),
        })
    }

    /// Go's concrete array type name, as printed in goccy MarshalerError text.
    fn go_array_name(&self) -> &'static str {
        match self {
            Self::Null => "Null",
            Self::Boolean => "Boolean",
            Self::Int8 => "Int8",
            Self::Int16 => "Int16",
            Self::Int32 => "Int32",
            Self::Int64 => "Int64",
            Self::UInt8 => "Uint8",
            Self::UInt16 => "Uint16",
            Self::UInt32 => "Uint32",
            Self::UInt64 => "Uint64",
            Self::Float16 => "Float16",
            Self::Float32 => "Float32",
            Self::Float64 => "Float64",
            Self::Utf8 => "String",
            Self::LargeUtf8 => "LargeString",
            Self::Binary => "Binary",
            Self::LargeBinary => "LargeBinary",
            Self::FixedSizeBinary => "FixedSizeBinary",
            Self::Date32 => "Date32",
            Self::Date64 => "Date64",
            Self::Time32(_) => "Time32",
            Self::Time64(_) => "Time64",
            Self::Timestamp(_) => "Timestamp",
            Self::Duration(_) => "Duration",
            Self::MonthInterval => "MonthInterval",
            Self::DayTimeInterval => "DayTimeInterval",
            Self::MonthDayNanoInterval => "MonthDayNanoInterval",
            Self::Decimal128 { .. } => "Decimal128",
            Self::Decimal256 { .. } => "Decimal256",
            Self::List { kind, .. } => match kind {
                ListKind::List => "List",
                ListKind::LargeList => "LargeList",
                ListKind::FixedSizeList => "FixedSizeList",
                ListKind::Map => "Map",
            },
            Self::Struct(_) => "Struct",
            Self::Union { dense: false, .. } => "SparseUnion",
            Self::Union { dense: true, .. } => "DenseUnion",
            Self::Dictionary(_) => "Dictionary",
            Self::RunEndEncoded { .. } => "RunEndEncoded",
        }
    }
}

fn bump(slots: &mut usize) -> usize {
    *slots += 1;
    *slots - 1
}

struct Prim<T: ArrowNativeType> {
    values: ScalarBuffer<T>,
    nulls: Option<NullBuffer>,
}
impl<T: ArrowNativeType> Prim<T> {
    fn get(&self, i: usize) -> T {
        self.values[i]
    }
}

/// Share the typed array's existing buffer; no buffer is re-imported, so
/// rendering never re-checks alignment of decoded IPC memory.
fn prim<A: ArrowPrimitiveType>(array: &dyn Array) -> Prim<A::Native> {
    let typed = array.as_primitive::<A>();
    Prim {
        values: typed.values().clone(),
        nulls: typed.nulls().cloned(),
    }
}

enum Offsets<'a> {
    I32(&'a [i32]),
    I64(&'a [i64]),
    Fixed(usize),
}
impl Offsets<'_> {
    fn range(&self, i: usize) -> (usize, usize) {
        match self {
            Self::I32(o) => (o[i].as_usize(), o[i + 1].as_usize()),
            Self::I64(o) => (o[i].as_usize(), o[i + 1].as_usize()),
            Self::Fixed(n) => (i * n, (i + 1) * n),
        }
    }
}

struct Bytes<'a> {
    offsets: Offsets<'a>,
    data: &'a [u8],
    nulls: Option<&'a NullBuffer>,
    /// Go reads a zero-length IPC buffer as a nil slice, so every binary value
    /// in such an array is a nil `[]byte`, which goccy encodes as `null`.
    nil_data: bool,
}

enum Col<'a> {
    Null,
    Boolean(&'a BooleanArray),
    Int8(Prim<i8>),
    Int16(Prim<i16>),
    Int32(Prim<i32>),
    Int64(Prim<i64>),
    UInt8(Prim<u8>),
    UInt16(Prim<u16>),
    UInt32(Prim<u32>),
    UInt64(Prim<u64>),
    Float16(Prim<F16>),
    Float32(Prim<f32>),
    Float64(Prim<f64>),
    Utf8(Bytes<'a>),
    Binary(Bytes<'a>),
    Date32(Prim<i32>),
    Date64(Prim<i64>),
    Time32(Prim<i32>, TimeUnit),
    Time64(Prim<i64>, TimeUnit),
    Timestamp(Prim<i64>, TimeUnit),
    Duration(Prim<i64>, TimeUnit),
    MonthInterval(Prim<i32>),
    DayTimeInterval(Prim<IntervalDayTime>),
    MonthDayNanoInterval(Prim<IntervalMonthDayNano>),
    Decimal128(Prim<i128>, u8, i8),
    Decimal256(Prim<i256>, u8, i8),
    List {
        offsets: Offsets<'a>,
        nulls: Option<&'a NullBuffer>,
        child: Box<Col<'a>>,
        plan: &'a Plan,
        slot: usize,
        elem_slots: usize,
        fixed: bool,
        /// Go's interval `MarshalJSON` marshals its stored slice directly
        /// when it has no nulls. A zero-length IPC buffer makes that slice
        /// nil, so when a List/LargeList's whole interval child is empty
        /// every non-null element renders `null`, not `[]`.
        nil_child: bool,
    },
    Struct {
        nulls: Option<&'a NullBuffer>,
        fields: Vec<Col<'a>>,
        order: &'a [(Box<[u8]>, usize)],
    },
    Union {
        array: &'a UnionArray,
        dense: bool,
        /// Child column per type id; ids are 0..=127.
        children: Vec<Option<Col<'a>>>,
    },
    Dictionary {
        nulls: Option<&'a NullBuffer>,
        keys: Vec<usize>,
        values: Box<Col<'a>>,
    },
    RunEndEncoded {
        array: &'a dyn Array,
        ends: RunEnds,
        values: Box<Col<'a>>,
    },
}

fn bytes<'a>(
    offsets: Offsets<'a>,
    data: &'a [u8],
    buffer_len: usize,
    nulls: Option<&'a NullBuffer>,
) -> Bytes<'a> {
    Bytes {
        offsets,
        data,
        nulls,
        nil_data: buffer_len == 0,
    }
}

impl<'a> Col<'a> {
    fn new(plan: &'a Plan, array: &'a dyn Array) -> Self {
        match plan {
            Plan::Null => Self::Null,
            Plan::Boolean => Self::Boolean(array.as_boolean()),
            Plan::Int8 => Self::Int8(prim::<Int8Type>(array)),
            Plan::Int16 => Self::Int16(prim::<Int16Type>(array)),
            Plan::Int32 => Self::Int32(prim::<Int32Type>(array)),
            Plan::Int64 => Self::Int64(prim::<Int64Type>(array)),
            Plan::UInt8 => Self::UInt8(prim::<UInt8Type>(array)),
            Plan::UInt16 => Self::UInt16(prim::<UInt16Type>(array)),
            Plan::UInt32 => Self::UInt32(prim::<UInt32Type>(array)),
            Plan::UInt64 => Self::UInt64(prim::<UInt64Type>(array)),
            Plan::Float16 => Self::Float16(prim::<Float16Type>(array)),
            Plan::Float32 => Self::Float32(prim::<Float32Type>(array)),
            Plan::Float64 => Self::Float64(prim::<Float64Type>(array)),
            // The session decodes string columns as binary so that invalid
            // UTF-8, which Go accepts, survives validated IPC decoding. The
            // plan still comes from the original schema, so render them as text.
            Plan::Utf8 => match array.as_string_opt::<i32>() {
                Some(a) => Self::Utf8(bytes(
                    Offsets::I32(a.value_offsets()),
                    a.value_data(),
                    a.values().len(),
                    a.nulls(),
                )),
                None => {
                    let a = array.as_binary::<i32>();
                    Self::Utf8(bytes(
                        Offsets::I32(a.value_offsets()),
                        a.value_data(),
                        a.values().len(),
                        a.nulls(),
                    ))
                }
            },
            Plan::LargeUtf8 => match array.as_string_opt::<i64>() {
                Some(a) => Self::Utf8(bytes(
                    Offsets::I64(a.value_offsets()),
                    a.value_data(),
                    a.values().len(),
                    a.nulls(),
                )),
                None => {
                    let a = array.as_binary::<i64>();
                    Self::Utf8(bytes(
                        Offsets::I64(a.value_offsets()),
                        a.value_data(),
                        a.values().len(),
                        a.nulls(),
                    ))
                }
            },
            Plan::Binary => {
                let a = array.as_binary::<i32>();
                Self::Binary(bytes(
                    Offsets::I32(a.value_offsets()),
                    a.value_data(),
                    a.values().len(),
                    a.nulls(),
                ))
            }
            Plan::LargeBinary => {
                let a = array.as_binary::<i64>();
                Self::Binary(bytes(
                    Offsets::I64(a.value_offsets()),
                    a.value_data(),
                    a.values().len(),
                    a.nulls(),
                ))
            }
            Plan::FixedSizeBinary => {
                let a = array.as_fixed_size_binary();
                let width = a.value_length().as_usize();
                // value_data() starts at the array's first value.
                Self::Binary(bytes(
                    Offsets::Fixed(width),
                    a.value_data(),
                    a.values().len(),
                    a.nulls(),
                ))
            }
            Plan::Date32 => Self::Date32(prim::<Date32Type>(array)),
            Plan::Date64 => Self::Date64(prim::<Date64Type>(array)),
            Plan::Time32(unit) => Self::Time32(
                match unit {
                    TimeUnit::Second => prim::<Time32SecondType>(array),
                    _ => prim::<Time32MillisecondType>(array),
                },
                *unit,
            ),
            Plan::Time64(unit) => Self::Time64(
                match unit {
                    TimeUnit::Microsecond => prim::<Time64MicrosecondType>(array),
                    _ => prim::<Time64NanosecondType>(array),
                },
                *unit,
            ),
            Plan::Timestamp(unit) => Self::Timestamp(
                match unit {
                    TimeUnit::Second => prim::<TimestampSecondType>(array),
                    TimeUnit::Millisecond => prim::<TimestampMillisecondType>(array),
                    TimeUnit::Microsecond => prim::<TimestampMicrosecondType>(array),
                    TimeUnit::Nanosecond => prim::<TimestampNanosecondType>(array),
                },
                *unit,
            ),
            Plan::Duration(unit) => Self::Duration(
                match unit {
                    TimeUnit::Second => prim::<DurationSecondType>(array),
                    TimeUnit::Millisecond => prim::<DurationMillisecondType>(array),
                    TimeUnit::Microsecond => prim::<DurationMicrosecondType>(array),
                    TimeUnit::Nanosecond => prim::<DurationNanosecondType>(array),
                },
                *unit,
            ),
            Plan::MonthInterval => Self::MonthInterval(prim::<IntervalYearMonthType>(array)),
            Plan::DayTimeInterval => Self::DayTimeInterval(prim::<IntervalDayTimeType>(array)),
            Plan::MonthDayNanoInterval => {
                Self::MonthDayNanoInterval(prim::<IntervalMonthDayNanoType>(array))
            }
            Plan::Decimal128 { precision, scale } => {
                Self::Decimal128(prim::<Decimal128Type>(array), *precision, *scale)
            }
            Plan::Decimal256 { precision, scale } => {
                Self::Decimal256(prim::<Decimal256Type>(array), *precision, *scale)
            }
            Plan::List {
                kind,
                slot,
                elem_slots,
                elem,
            } => {
                let (offsets, values): (Offsets<'a>, &'a dyn Array) = match kind {
                    ListKind::List => {
                        let a = array.as_list::<i32>();
                        (Offsets::I32(a.value_offsets()), a.values().as_ref())
                    }
                    ListKind::LargeList => {
                        let a = array.as_list::<i64>();
                        (Offsets::I64(a.value_offsets()), a.values().as_ref())
                    }
                    ListKind::FixedSizeList => {
                        let a = array.as_fixed_size_list();
                        (
                            Offsets::Fixed(a.value_length().as_usize()),
                            a.values().as_ref(),
                        )
                    }
                    ListKind::Map => {
                        let a = array.as_map();
                        (Offsets::I32(a.value_offsets()), a.entries() as &dyn Array)
                    }
                };
                Self::List {
                    offsets,
                    nulls: array.nulls(),
                    child: Box::new(Col::new(elem, values)),
                    plan: elem,
                    slot: *slot,
                    elem_slots: *elem_slots,
                    fixed: *kind == ListKind::FixedSizeList,
                    nil_child: matches!(kind, ListKind::List | ListKind::LargeList)
                        && matches!(
                            elem.as_ref(),
                            Plan::MonthInterval
                                | Plan::DayTimeInterval
                                | Plan::MonthDayNanoInterval
                        )
                        && values.is_empty(),
                }
            }
            Plan::Struct(object) => {
                let a = array.as_struct();
                Self::Struct {
                    nulls: a.nulls(),
                    fields: object
                        .fields
                        .iter()
                        .zip(a.columns())
                        .map(|(plan, column)| Col::new(plan, column.as_ref()))
                        .collect(),
                    order: &object.order,
                }
            }
            Plan::Union { dense, children } => {
                let a = array.as_union();
                let mut cols: Vec<Option<Col<'a>>> = (0..128).map(|_| None).collect();
                for (id, plan) in children {
                    if let Ok(index) = usize::try_from(*id) {
                        cols[index] = Some(Col::new(plan, a.child(*id).as_ref()));
                    }
                }
                Self::Union {
                    array: a,
                    dense: *dense,
                    children: cols,
                }
            }
            Plan::Dictionary(values) => {
                let a = array.as_any_dictionary();
                let keys = if a.values().is_empty() {
                    vec![0; a.len()]
                } else {
                    a.normalized_keys()
                };
                Self::Dictionary {
                    nulls: a.keys().nulls(),
                    keys,
                    values: Box::new(Col::new(values, a.values().as_ref())),
                }
            }
            Plan::RunEndEncoded { ends, values } => {
                let child = array.as_any_ree().values();
                Self::RunEndEncoded {
                    array,
                    ends: *ends,
                    values: Box::new(Col::new(values, child.as_ref())),
                }
            }
        }
    }

    fn ree_index(array: &dyn Array, ends: RunEnds, i: usize) -> usize {
        match ends {
            RunEnds::I16 => array.as_run::<Int16Type>().run_ends().get_physical_index(i),
            RunEnds::I32 => array.as_run::<Int32Type>().run_ends().get_physical_index(i),
            RunEnds::I64 => array.as_run::<Int64Type>().run_ends().get_physical_index(i),
        }
    }

    fn union_target(array: &UnionArray, dense: bool, i: usize) -> (usize, usize) {
        let id = array.type_id(i);
        let index = if dense { array.value_offset(i) } else { i };
        (usize::try_from(id).unwrap_or(0), index)
    }

    /// Go's `IsNull`: the validity bitmap only. Unions use it for their
    /// selected child, so a child whose value is nil for another reason
    /// renders `[type_code,null]` rather than `null`. Go's Null arrays clear
    /// their bitmap, and union and REE arrays have none; a dictionary's bitmap
    /// is its keys'.
    fn validity_null(&self, i: usize) -> bool {
        match self {
            Self::Null | Self::Union { .. } | Self::RunEndEncoded { .. } => false,
            Self::Dictionary { nulls, .. } => bit(*nulls, i),
            bitmap => bitmap.is_null(i),
        }
    }

    /// Whether Go's `GetOneForMarshal` returns nil for this value.
    fn is_null(&self, i: usize) -> bool {
        match self {
            Self::Null => true,
            Self::Boolean(a) => a.is_null(i),
            Self::Int8(p) => bit(p.nulls.as_ref(), i),
            Self::Int16(p) => bit(p.nulls.as_ref(), i),
            Self::Int32(p) | Self::Date32(p) | Self::Time32(p, _) | Self::MonthInterval(p) => {
                bit(p.nulls.as_ref(), i)
            }
            Self::Int64(p)
            | Self::Date64(p)
            | Self::Time64(p, _)
            | Self::Timestamp(p, _)
            | Self::Duration(p, _) => bit(p.nulls.as_ref(), i),
            Self::DayTimeInterval(p) => bit(p.nulls.as_ref(), i),
            Self::UInt8(p) => bit(p.nulls.as_ref(), i),
            Self::UInt16(p) => bit(p.nulls.as_ref(), i),
            Self::Float16(p) => bit(p.nulls.as_ref(), i),
            Self::UInt32(p) => bit(p.nulls.as_ref(), i),
            Self::UInt64(p) => bit(p.nulls.as_ref(), i),
            Self::Float32(p) => bit(p.nulls.as_ref(), i),
            Self::Float64(p) => bit(p.nulls.as_ref(), i),
            Self::MonthDayNanoInterval(p) => bit(p.nulls.as_ref(), i),
            Self::Decimal128(p, ..) => bit(p.nulls.as_ref(), i),
            Self::Decimal256(p, ..) => bit(p.nulls.as_ref(), i),
            Self::Utf8(b) | Self::Binary(b) => bit(b.nulls, i),
            Self::List { nulls, .. } | Self::Struct { nulls, .. } => bit(*nulls, i),
            Self::Union {
                array,
                dense,
                children,
            } => {
                let (id, index) = Self::union_target(array, *dense, i);
                children[id]
                    .as_ref()
                    .is_none_or(|child| child.validity_null(index))
            }
            Self::Dictionary {
                nulls,
                keys,
                values,
            } => bit(*nulls, i) || values.is_null(keys[i]),
            Self::RunEndEncoded {
                array,
                ends,
                values,
            } => values.is_null(Self::ree_index(*array, *ends, i)),
        }
    }

    /// Go's GetOneForMarshal stage: marshal every list-like value reachable
    /// without crossing a list into its slot, in Go's evaluation order.
    fn gather(&self, i: usize, cx: &mut Cx) -> Result<(), Fail> {
        self.gather_as(i, cx, false)
    }

    /// Gather for one element of an enclosing marshal run. Go's
    /// FixedSizeList.MarshalJSON encodes each element slice and returns its
    /// error rather than panicking in GetOneForMarshal. The enclosing run
    /// then fails, and the outer list panics with both wrappers.
    fn gather_element(&self, i: usize, cx: &mut Cx) -> Result<(), Fail> {
        self.gather_as(i, cx, matches!(self, Self::List { fixed: true, .. }))
    }

    fn gather_as(&self, i: usize, cx: &mut Cx, return_error: bool) -> Result<(), Fail> {
        match self {
            Self::List {
                offsets,
                nulls,
                child,
                plan,
                slot,
                elem_slots,
                nil_child,
                ..
            } => {
                if nulls.is_some_and(|n| n.is_null(i)) {
                    return Ok(());
                }
                let depth = cx.depth;
                if *nil_child {
                    let buffer = &mut cx.scopes[depth][*slot];
                    buffer.clear();
                    buffer.extend_from_slice(b"null");
                    return Ok(());
                }
                let (start, end) = offsets.range(i);
                let mut buffer = std::mem::take(&mut cx.scopes[depth][*slot]);
                buffer.clear();
                let result = marshal_run(child, start, end, *elem_slots, &mut buffer, cx);
                cx.scopes[depth][*slot] = buffer;
                result.map_err(|fail| match fail {
                    Fail::Encode(message) => {
                        let wrapped = format!(
                            "json: error calling MarshalJSON for type *array.{}: {message}",
                            plan.go_array_name()
                        );
                        if return_error {
                            Fail::Encode(wrapped)
                        } else {
                            Fail::Panic(wrapped)
                        }
                    }
                    panic => panic,
                })
            }
            Self::Struct { nulls, fields, .. } => {
                if nulls.is_some_and(|n| n.is_null(i)) {
                    return Ok(());
                }
                fields.iter().try_for_each(|field| field.gather(i, cx))
            }
            Self::Union {
                array,
                dense,
                children,
            } => {
                let (id, index) = Self::union_target(array, *dense, i);
                match &children[id] {
                    Some(child) if !child.validity_null(index) => child.gather(index, cx),
                    _ => Ok(()),
                }
            }
            Self::Dictionary {
                nulls,
                keys,
                values,
            } => {
                if nulls.is_some_and(|n| n.is_null(i)) {
                    return Ok(());
                }
                values.gather(keys[i], cx)
            }
            Self::RunEndEncoded {
                array,
                ends,
                values,
            } => values.gather(Self::ree_index(*array, *ends, i), cx),
            _ => Ok(()),
        }
    }

    /// goccy's encoding of the value GetOneForMarshal produced.
    fn encode(&self, i: usize, out: &mut Vec<u8>, cx: &mut Cx) -> Result<(), Fail> {
        if self.is_null(i) {
            out.extend_from_slice(b"null");
            return Ok(());
        }
        match self {
            Self::Null => out.extend_from_slice(b"null"),
            Self::Boolean(a) => out.extend_from_slice(if a.value(i) { b"true" } else { b"false" }),
            Self::Int8(p) => push_i64(out, p.get(i).into()),
            Self::Int16(p) => push_i64(out, p.get(i).into()),
            Self::Int32(p) => push_i64(out, p.get(i).into()),
            Self::Int64(p) => push_i64(out, p.get(i)),
            Self::UInt8(p) => push_u64(out, p.get(i).into()),
            Self::UInt16(p) => push_u64(out, p.get(i).into()),
            Self::UInt32(p) => push_u64(out, p.get(i).into()),
            Self::UInt64(p) => push_u64(out, p.get(i)),
            Self::Float16(p) => push_f32(out, go_f16_to_f32(p.get(i).to_bits()), cx),
            Self::Float32(p) => push_f32(out, p.get(i), cx),
            Self::Float64(p) => push_f64(out, p.get(i))?,
            Self::Utf8(b) => {
                let (start, end) = b.offsets.range(i);
                write_str(out, &b.data[start..end]);
            }
            Self::Binary(b) => {
                if b.nil_data {
                    out.extend_from_slice(b"null");
                } else {
                    let (start, end) = b.offsets.range(i);
                    push_base64(out, &b.data[start..end]);
                }
            }
            Self::Date32(p) => {
                out.push(b'"');
                push_date(out, i64::from(p.get(i)));
                out.push(b'"');
            }
            Self::Date64(p) => {
                out.push(b'"');
                // Go truncates milliseconds toward zero into whole days.
                push_date(out, p.get(i) / 86_400_000);
                out.push(b'"');
            }
            Self::Time32(p, unit) => {
                out.push(b'"');
                let (abs, nanos) = go_abs_time(i64::from(p.get(i)), *unit, false);
                push_clock(out, abs, nanos);
                out.push(b'"');
            }
            Self::Time64(p, unit) => {
                out.push(b'"');
                let (abs, nanos) = go_abs_time(p.get(i), *unit, false);
                push_clock(out, abs, nanos);
                out.push(b'"');
            }
            Self::Timestamp(p, unit) => {
                out.push(b'"');
                let (abs, nanos) = go_abs_time(p.get(i), *unit, true);
                push_abs_date(out, abs);
                out.push(b' ');
                push_clock(out, abs, nanos);
                out.push(b'"');
            }
            Self::Duration(p, unit) => {
                out.push(b'"');
                push_i64(out, p.get(i));
                out.extend_from_slice(unit_suffix(*unit).as_bytes());
                out.push(b'"');
            }
            Self::MonthInterval(p) => {
                out.extend_from_slice(b"{\"months\":");
                push_i64(out, p.get(i).into());
                out.push(b'}');
            }
            Self::DayTimeInterval(p) => {
                let value = p.get(i);
                out.extend_from_slice(b"{\"days\":");
                push_i64(out, value.days.into());
                out.extend_from_slice(b",\"milliseconds\":");
                push_i64(out, value.milliseconds.into());
                out.push(b'}');
            }
            Self::MonthDayNanoInterval(p) => {
                let value = p.get(i);
                out.extend_from_slice(b"{\"months\":");
                push_i64(out, value.months.into());
                out.extend_from_slice(b",\"days\":");
                push_i64(out, value.days.into());
                out.extend_from_slice(b",\"nanoseconds\":");
                push_i64(out, value.nanoseconds);
                out.push(b'}');
            }
            Self::Decimal128(p, precision, scale) => {
                let value = p.get(i);
                let magnitude = value.unsigned_abs();
                let limbs = [magnitude as u64, (magnitude >> 64) as u64];
                push_decimal(out, value < 0, &limbs, *precision, *scale);
            }
            Self::Decimal256(p, precision, scale) => {
                let bytes = p.get(i).to_le_bytes();
                let negative = bytes[31] & 0x80 != 0;
                let mut limbs = [0u64; 4];
                for (index, limb) in limbs.iter_mut().enumerate() {
                    *limb = u64::from_le_bytes(
                        bytes[index * 8..index * 8 + 8].try_into().expect("8 bytes"),
                    );
                }
                if negative {
                    let mut carry = true;
                    for limb in &mut limbs {
                        let (value, overflow) = (!*limb).overflowing_add(u64::from(carry));
                        *limb = value;
                        carry = overflow;
                    }
                }
                push_decimal(out, negative, &limbs, *precision, *scale);
            }
            Self::List { slot, .. } => out.extend_from_slice(&cx.scopes[cx.depth][*slot]),
            Self::Struct { fields, order, .. } => encode_object(order, fields, i, out, cx)?,
            Self::Union {
                array,
                dense,
                children,
            } => {
                let (id, index) = Self::union_target(array, *dense, i);
                out.push(b'[');
                push_i64(out, i64::from(array.type_id(i)));
                out.push(b',');
                match &children[id] {
                    Some(child) => child.encode(index, out, cx)?,
                    None => out.extend_from_slice(b"null"),
                }
                out.push(b']');
            }
            Self::Dictionary { keys, values, .. } => values.encode(keys[i], out, cx)?,
            Self::RunEndEncoded {
                array,
                ends,
                values,
            } => values.encode(Self::ree_index(*array, *ends, i), out, cx)?,
        }
        Ok(())
    }
}

fn bit(nulls: Option<&NullBuffer>, i: usize) -> bool {
    nulls.is_some_and(|n| n.is_null(i))
}

fn encode_object(
    order: &[(Box<[u8]>, usize)],
    fields: &[Col<'_>],
    i: usize,
    out: &mut Vec<u8>,
    cx: &mut Cx,
) -> Result<(), Fail> {
    out.push(b'{');
    for (position, (key, index)) in order.iter().enumerate() {
        if position > 0 {
            out.push(b',');
        }
        out.extend_from_slice(key);
        out.push(b':');
        fields[*index].encode(i, out, cx)?;
    }
    out.push(b'}');
    Ok(())
}

/// A list-like value's `json.Marshal(slice)`: each element's own
/// GetOneForMarshal then encoding, followed by goccy's compaction of the
/// Marshaler output, which rejects raw float32 non-finite tokens.
fn marshal_run(
    child: &Col<'_>,
    start: usize,
    end: usize,
    slots: usize,
    out: &mut Vec<u8>,
    cx: &mut Cx,
) -> Result<(), Fail> {
    let saved_raw = cx.raw.take();
    cx.depth += 1;
    if cx.scopes.len() <= cx.depth {
        cx.scopes.push(Vec::new());
    }
    let depth = cx.depth;
    if cx.scopes[depth].len() < slots {
        cx.scopes[depth].resize_with(slots, Vec::new);
    }
    out.push(b'[');
    // Dictionary.MarshalJSON evaluates every element's GetOneForMarshal
    // before encoding any of them, so a later panic beats an earlier encoder
    // error. Gathering is deterministic, so the main loop repeats it.
    let mut result = if matches!(child, Col::Dictionary { .. }) {
        (start..end).try_for_each(|index| child.gather(index, cx))
    } else {
        Ok(())
    };
    if result.is_ok() {
        for index in start..end {
            if index > start {
                out.push(b',');
            }
            result = child
                .gather_element(index, cx)
                .and_then(|()| child.encode(index, out, cx));
            if result.is_err() {
                break;
            }
        }
    }
    out.push(b']');
    cx.depth -= 1;
    let raw = std::mem::replace(&mut cx.raw, saved_raw);
    result?;
    match raw {
        None => Ok(()),
        Some(RawToken::NaN) => Err(Fail::Encode("unexpected character 'N'".into())),
        Some(RawToken::PosInf) => Err(Fail::Encode("unexpected character '+'".into())),
        Some(RawToken::NegInf) => Err(Fail::Encode(
            "strconv.ParseFloat: parsing \"-\": invalid syntax".into(),
        )),
    }
}

/// goccy's HTML-escaping, UTF-8-normalizing string encoder: `"`, `\`, `\n`,
/// `\r`, `\t` short escapes, `<>&` and other C0 controls as `\u00xx`, each
/// invalid UTF-8 byte as `�`, and U+2028/U+2029 escaped. DEL and other
/// valid UTF-8 pass through raw.
fn write_str(out: &mut Vec<u8>, s: &[u8]) {
    out.push(b'"');
    let mut start = 0;
    let mut j = 0;
    while j < s.len() {
        let c = s[j];
        let escape: Option<&[u8]> = match c {
            b'"' => Some(b"\\\""),
            b'\\' => Some(b"\\\\"),
            b'\n' => Some(b"\\n"),
            b'\r' => Some(b"\\r"),
            b'\t' => Some(b"\\t"),
            b'<' | b'>' | b'&' | 0x00..=0x1f => None,
            0x20..=0x7f => {
                j += 1;
                continue;
            }
            _ => {
                let (state, size) = decode_rune(&s[j..]);
                let replacement: &[u8] = match state {
                    RuneState::Valid => {
                        j += size;
                        continue;
                    }
                    RuneState::Error => b"\\ufffd",
                    RuneState::LineSep => b"\\u2028",
                    RuneState::ParagraphSep => b"\\u2029",
                };
                out.extend_from_slice(&s[start..j]);
                out.extend_from_slice(replacement);
                j += size;
                start = j;
                continue;
            }
        };
        out.extend_from_slice(&s[start..j]);
        match escape {
            Some(text) => out.extend_from_slice(text),
            None => {
                const HEX: &[u8; 16] = b"0123456789abcdef";
                out.extend_from_slice(b"\\u00");
                out.push(HEX[usize::from(c >> 4)]);
                out.push(HEX[usize::from(c & 0xf)]);
            }
        }
        j += 1;
        start = j;
    }
    out.extend_from_slice(&s[start..]);
    out.push(b'"');
}

#[derive(PartialEq, Eq, Debug)]
enum RuneState {
    Valid,
    Error,
    LineSep,
    ParagraphSep,
}

/// goccy's decodeRuneInString for a non-ASCII lead byte. An invalid
/// sequence consumes exactly one byte.
fn decode_rune(s: &[u8]) -> (RuneState, usize) {
    let s0 = s[0];
    let (size, low, high) = match s0 {
        0xc2..=0xdf => (2, 0x80, 0xbf),
        0xe0 => (3, 0xa0, 0xbf),
        0xe1..=0xec | 0xee..=0xef => (3, 0x80, 0xbf),
        0xed => (3, 0x80, 0x9f),
        0xf0 => (4, 0x90, 0xbf),
        0xf1..=0xf3 => (4, 0x80, 0xbf),
        0xf4 => (4, 0x80, 0x8f),
        _ => return (RuneState::Error, 1),
    };
    if s.len() < size || !(low..=high).contains(&s[1]) {
        return (RuneState::Error, 1);
    }
    if size > 2 && !(0x80..=0xbf).contains(&s[2]) {
        return (RuneState::Error, 1);
    }
    if size == 3 && s0 == 0xe2 && s[1] == 0x80 {
        match s[2] {
            0xa8 => return (RuneState::LineSep, 3),
            0xa9 => return (RuneState::ParagraphSep, 3),
            _ => {}
        }
    }
    if size > 3 && !(0x80..=0xbf).contains(&s[3]) {
        return (RuneState::Error, 1);
    }
    (RuneState::Valid, size)
}

fn push_u64(out: &mut Vec<u8>, mut value: u64) {
    let mut digits = [0u8; 20];
    let mut at = digits.len();
    loop {
        at -= 1;
        digits[at] = b'0' + (value % 10) as u8;
        value /= 10;
        if value == 0 {
            break;
        }
    }
    out.extend_from_slice(&digits[at..]);
}

fn push_i64(out: &mut Vec<u8>, value: i64) {
    if value < 0 {
        out.push(b'-');
    }
    push_u64(out, value.unsigned_abs());
}

fn push_base64(out: &mut Vec<u8>, data: &[u8]) {
    use base64::Engine;
    let engine = &base64::engine::general_purpose::STANDARD;
    out.push(b'"');
    let start = out.len();
    out.resize(
        start + base64::encoded_len(data.len(), true).expect("bounded base64 length"),
        0,
    );
    let written = engine
        .encode_slice(data, &mut out[start..])
        .expect("sized base64 output");
    out.truncate(start + written);
    out.push(b'"');
}

/// Go Arrow v13 `float16.Num.Float32`. It maps exponent 0 to float32 exponent
/// 0, so half subnormals become float32 subnormals (`0x0001` is
/// 1.1479437e-41, not 2^-24). Normal, infinite and NaN values convert exactly.
fn go_f16_to_f32(bits: u16) -> f32 {
    let sign = u32::from(bits >> 15) << 31;
    let exponent = u32::from((bits >> 10) & 0x1f);
    let fraction = u32::from(bits & 0x3ff);
    let exponent = match exponent {
        0 => 0,
        0x1f => 0xff,
        e => e + 127 - 15,
    };
    f32::from_bits(sign | (exponent << 23) | (fraction << 13))
}

/// Go `strconv.AppendFloat(b, v, 'f' or 'e', -1, bits)` from shortest
/// round-trip digits. The digits come from zmij, which breaks ties between
/// two shortest candidates toward the even digit as Go does. Core's `{:e}`
/// rounds such ties up (`2079541830868280.3` where Go prints `.2`); the SQL
/// renderer relies on the same property. `text` is zmij's output in any of
/// its notations; only the digits, sign and decimal exponent are used. goccy
/// does not normalize exponents as encoding/json does (`1e-07`, `1e+21`).
fn push_go_float(out: &mut Vec<u8>, scientific: bool, text: &str) {
    let text = text.as_bytes();
    let (negative, text) = match text.split_first() {
        Some((b'-', rest)) => (true, rest),
        _ => (false, text),
    };
    let (mantissa, exponent) = match text.iter().position(|&b| b == b'e' || b == b'E') {
        Some(e) => (
            &text[..e],
            std::str::from_utf8(&text[e + 1..])
                .expect("ASCII exponent")
                .parse::<i32>()
                .expect("integer exponent"),
        ),
        None => (text, 0),
    };
    let integer_digits = mantissa
        .iter()
        .position(|&b| b == b'.')
        .unwrap_or(mantissa.len());
    let mut all = [0u8; 64];
    let mut count = 0;
    for &b in mantissa.iter().filter(|b| b.is_ascii_digit()) {
        all[count] = b;
        count += 1;
    }
    let leading = all[..count].iter().take_while(|&&b| b == b'0').count();
    let mut end = count;
    while end > leading && all[end - 1] == b'0' {
        end -= 1;
    }
    // Scientific exponent of the first significant digit; zero is "0" e0.
    let (digits, exponent): (&[u8], i32) = if end == leading {
        (b"0", 0)
    } else {
        (
            &all[leading..end],
            integer_digits as i32 - leading as i32 - 1 + exponent,
        )
    };
    if negative {
        out.push(b'-');
    }
    if scientific {
        out.push(digits[0]);
        if digits.len() > 1 {
            out.push(b'.');
            out.extend_from_slice(&digits[1..]);
        }
        out.push(b'e');
        out.push(if exponent < 0 { b'-' } else { b'+' });
        let magnitude = exponent.unsigned_abs();
        if magnitude < 10 {
            out.push(b'0');
        }
        push_u64(out, u64::from(magnitude));
    } else {
        let point = exponent + 1;
        if point <= 0 {
            out.extend_from_slice(b"0.");
            out.resize(out.len() + point.unsigned_abs() as usize, b'0');
            out.extend_from_slice(digits);
        } else {
            let point = point as usize;
            if point >= digits.len() {
                out.extend_from_slice(digits);
                out.resize(out.len() + point - digits.len(), b'0');
            } else {
                out.extend_from_slice(&digits[..point]);
                out.push(b'.');
                out.extend_from_slice(&digits[point..]);
            }
        }
    }
}

fn push_f64(out: &mut Vec<u8>, value: f64) -> Result<(), Fail> {
    if !value.is_finite() {
        let text = if value.is_nan() {
            "NaN"
        } else if value > 0.0 {
            "+Inf"
        } else {
            "-Inf"
        };
        return Err(Fail::Encode(format!("json: unsupported value: {text}")));
    }
    let abs = value.abs();
    let scientific = abs != 0.0 && !(1e-6..1e21).contains(&abs);
    push_go_float(out, scientific, zmij::Buffer::new().format_finite(value));
    Ok(())
}

/// goccy's float32 path has no finiteness check and emits strconv's tokens.
fn push_f32(out: &mut Vec<u8>, value: f32, cx: &mut Cx) {
    if !value.is_finite() {
        let (text, token): (&[u8], _) = if value.is_nan() {
            (b"NaN", RawToken::NaN)
        } else if value > 0.0 {
            (b"+Inf", RawToken::PosInf)
        } else {
            (b"-Inf", RawToken::NegInf)
        };
        out.extend_from_slice(text);
        cx.raw.get_or_insert(token);
        return;
    }
    let abs = value.abs();
    let scientific = abs != 0.0 && !(1e-6..1e21).contains(&abs);
    push_go_float(out, scientific, zmij::Buffer::new().format_finite(value));
}

fn unit_suffix(unit: TimeUnit) -> &'static str {
    match unit {
        TimeUnit::Second => "s",
        TimeUnit::Millisecond => "ms",
        TimeUnit::Microsecond => "us",
        TimeUnit::Nanosecond => "ns",
    }
}

/// Go's `time.Unix(...)` as used by Arrow v13: whole seconds for Timestamp
/// seconds, otherwise `time.Unix(0, v*multiplier)` with int64 wrapping. It
/// returns Go's "absolute" seconds (`unixSec + unixToInternal +
/// internalToAbsolute`, also wrapping, as in `Time.locabs`) and the
/// nanosecond remainder. Go formats every date and clock field from that value.
fn go_abs_time(value: i64, unit: TimeUnit, timestamp: bool) -> (u64, u32) {
    const UNIX_TO_ABSOLUTE: i64 = 9_223_372_028_741_760_000;
    let (sec, nanos) = if timestamp && unit == TimeUnit::Second {
        (value, 0)
    } else {
        let multiplier = match unit {
            TimeUnit::Second => 1_000_000_000,
            TimeUnit::Millisecond => 1_000_000,
            TimeUnit::Microsecond => 1_000,
            TimeUnit::Nanosecond => 1,
        };
        let nanos = value.wrapping_mul(multiplier);
        (
            nanos.div_euclid(1_000_000_000),
            nanos.rem_euclid(1_000_000_000) as u32,
        )
    };
    (sec.wrapping_add(UNIX_TO_ABSOLUTE) as u64, nanos)
}

/// Go 1.25 `absDays.date` for an absolute-seconds value, written as
/// "2006-01-02". Wrapped extremes therefore print Go's (wrapped) dates.
fn push_abs_date(out: &mut Vec<u8>, abs: u64) {
    const ABSOLUTE_YEARS: u64 = 292_277_022_400;
    let days = abs / 86_400;
    let d = 4 * days + 3;
    let century = d / 146_097;
    let cd = (d % 146_097) as u32 | 3;
    let product = 2_939_745u64 * u64::from(cd);
    let cyear = (product >> 32) as u32;
    let ayday = (product as u32) / 2_939_745 / 4;
    let m = 2141 * ayday + 197_913;
    let jan_feb = u32::from(ayday >= 306);
    let year = (century.wrapping_mul(100).wrapping_sub(ABSOLUTE_YEARS) as i64)
        .wrapping_add(i64::from(cyear))
        .wrapping_add(i64::from(jan_feb));
    push_year_month_day(out, year, (m >> 16) - jan_feb * 12, 1 + (m & 0xffff) / 2141);
}

/// Go layout "2006-01-02" for days since the Unix epoch.
fn push_date(out: &mut Vec<u8>, days: i64) {
    let (year, month, day) = civil_from_days(days);
    push_year_month_day(out, year, month, day);
}

fn push_year_month_day(out: &mut Vec<u8>, year: i64, month: u32, day: u32) {
    // Go appendInt(year, 4): sign, then at least four digits.
    if year < 0 {
        out.push(b'-');
    }
    let magnitude = year.unsigned_abs();
    for divisor in [1000, 100, 10] {
        if magnitude < divisor {
            out.push(b'0');
        }
    }
    push_u64(out, magnitude);
    out.push(b'-');
    push_two(out, month);
    out.push(b'-');
    push_two(out, day);
}

/// Go layout "15:04:05.999999999" from absolute seconds (`absSeconds.clock`).
fn push_clock(out: &mut Vec<u8>, abs: u64, nanos: u32) {
    let of_day = (abs % 86_400) as u32;
    push_two(out, of_day / 3600);
    out.push(b':');
    push_two(out, of_day / 60 % 60);
    out.push(b':');
    push_two(out, of_day % 60);
    if nanos != 0 {
        let mut digits = [0u8; 9];
        let mut rest = nanos;
        for digit in digits.iter_mut().rev() {
            *digit = b'0' + (rest % 10) as u8;
            rest /= 10;
        }
        let len = 9 - digits.iter().rev().take_while(|&&d| d == b'0').count();
        out.push(b'.');
        out.extend_from_slice(&digits[..len]);
    }
}

fn push_two(out: &mut Vec<u8>, value: u32) {
    out.push(b'0' + (value / 10) as u8);
    out.push(b'0' + (value % 10) as u8);
}

/// Proleptic Gregorian date for days since 1970-01-01 (Hinnant's algorithm).
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = i128::from(days) + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let year = yoe + era * 400 + i128::from(month <= 2);
    (year as i64, month, day)
}

// ---- Decimal: big.Float(int).Quo(10^scale as float64).Text('g', precision) ----

/// Little-endian u64 limbs without trailing zero limbs.
type Big = Vec<u64>;

fn big_trim(mut a: Big) -> Big {
    while a.last() == Some(&0) {
        a.pop();
    }
    a
}

fn big_bits(a: &[u64]) -> usize {
    a.last().map_or(0, |top| {
        (a.len() - 1) * 64 + (64 - top.leading_zeros() as usize)
    })
}

fn big_shl(a: &[u64], bits: usize) -> Big {
    let (words, shift) = (bits / 64, bits % 64);
    let mut result = vec![0u64; a.len() + words + 1];
    for (index, &limb) in a.iter().enumerate() {
        result[index + words] |= limb << shift;
        if shift > 0 {
            result[index + words + 1] |= limb >> (64 - shift);
        }
    }
    big_trim(result)
}

fn big_shr(a: &[u64], bits: usize) -> Big {
    let (words, shift) = (bits / 64, bits % 64);
    if words >= a.len() {
        return Vec::new();
    }
    let mut result = vec![0u64; a.len() - words];
    for index in 0..result.len() {
        let low = a[index + words] >> shift;
        let high = if shift > 0 {
            a.get(index + words + 1)
                .map_or(0, |&next| next << (64 - shift))
        } else {
            0
        };
        result[index] = low | high;
    }
    big_trim(result)
}

fn big_bit(a: &[u64], bit: usize) -> bool {
    a.get(bit / 64)
        .is_some_and(|limb| limb >> (bit % 64) & 1 == 1)
}

fn big_low_nonzero(a: &[u64], bits: usize) -> bool {
    let words = bits / 64;
    a.iter().take(words).any(|&limb| limb != 0)
        || (!bits.is_multiple_of(64)
            && a.get(words)
                .is_some_and(|limb| limb & ((1u64 << (bits % 64)) - 1) != 0))
}

fn big_divmod(a: &[u64], divisor: u64) -> (Big, u64) {
    let mut quotient = vec![0u64; a.len()];
    let mut remainder = 0u128;
    for index in (0..a.len()).rev() {
        let current = (remainder << 64) | u128::from(a[index]);
        quotient[index] = (current / u128::from(divisor)) as u64;
        remainder = current % u128::from(divisor);
    }
    (big_trim(quotient), remainder as u64)
}

fn big_mul_small(a: &mut Big, factor: u64) {
    let mut carry = 0u128;
    for limb in a.iter_mut() {
        let product = u128::from(*limb) * u128::from(factor) + carry;
        *limb = product as u64;
        carry = product >> 64;
    }
    if carry > 0 {
        a.push(carry as u64);
    }
}

fn big_add_one(a: &mut Big) {
    for limb in a.iter_mut() {
        let (value, overflow) = limb.overflowing_add(1);
        *limb = value;
        if !overflow {
            return;
        }
    }
    a.push(1);
}

fn big_decimal(mut a: Big) -> Vec<u8> {
    const CHUNK: u64 = 10_000_000_000_000_000_000;
    let mut chunks = Vec::new();
    while !a.is_empty() {
        let (quotient, remainder) = big_divmod(&a, CHUNK);
        chunks.push(remainder);
        a = quotient;
    }
    let mut digits = Vec::new();
    for (index, chunk) in chunks.iter().rev().enumerate() {
        let text = chunk.to_string();
        if index > 0 {
            digits.resize(digits.len() + 19 - text.len(), b'0');
        }
        digits.extend_from_slice(text.as_bytes());
    }
    digits
}

/// Go `math.Pow10`, including its table-product rounding for large powers.
fn go_pow10(n: i32) -> f64 {
    const TAB: [f64; 32] = [
        1e0, 1e1, 1e2, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8, 1e9, 1e10, 1e11, 1e12, 1e13, 1e14, 1e15, 1e16,
        1e17, 1e18, 1e19, 1e20, 1e21, 1e22, 1e23, 1e24, 1e25, 1e26, 1e27, 1e28, 1e29, 1e30, 1e31,
    ];
    const POS: [f64; 10] = [
        1e0, 1e32, 1e64, 1e96, 1e128, 1e160, 1e192, 1e224, 1e256, 1e288,
    ];
    const NEG: [f64; 11] = [
        1e-0, 1e-32, 1e-64, 1e-96, 1e-128, 1e-160, 1e-192, 1e-224, 1e-256, 1e-288, 1e-320,
    ];
    if (0..=308).contains(&n) {
        let n = n as usize;
        POS[n / 32] * TAB[n % 32]
    } else if (-323..0).contains(&n) {
        let n = n.unsigned_abs() as usize;
        NEG[n / 32] / TAB[n % 32]
    } else if n > 0 {
        f64::INFINITY
    } else {
        0.0
    }
}

/// Exact emulation of Go Arrow's decimal GetOneForMarshal string.
fn push_decimal(out: &mut Vec<u8>, negative: bool, limbs: &[u64], precision: u8, scale: i8) {
    out.push(b'"');
    out.extend_from_slice(&decimal_text(negative, limbs, precision, scale));
    out.push(b'"');
}

fn decimal_text(negative: bool, limbs: &[u64], precision: u8, scale: i8) -> Vec<u8> {
    let magnitude = big_trim(limbs.to_vec());
    if magnitude.is_empty() {
        return b"0".to_vec();
    }
    // big.Float.SetInt: precision is max(64, bit length); Quo keeps it.
    let prec = big_bits(&magnitude).max(64);
    let divisor = go_pow10(i32::from(scale));
    let bits = divisor.to_bits();
    let raw_exponent = ((bits >> 52) & 0x7ff) as i64;
    let fraction = bits & ((1u64 << 52) - 1);
    let (divisor_mantissa, divisor_exponent) = if raw_exponent == 0 {
        (fraction, -1074)
    } else {
        (fraction | (1u64 << 52), raw_exponent - 1075)
    };
    // Quotient with at least prec+1 significant bits plus a sticky bit.
    let shift = (prec + 2 + 64).saturating_sub(big_bits(&magnitude));
    let (quotient, remainder) = big_divmod(&big_shl(&magnitude, shift), divisor_mantissa);
    let excess = big_bits(&quotient) - prec;
    let mut mantissa = big_shr(&quotient, excess);
    let half = big_bit(&quotient, excess - 1);
    let rest = remainder != 0 || big_low_nonzero(&quotient, excess - 1);
    if half && (rest || mantissa.first().is_some_and(|low| low & 1 == 1)) {
        big_add_one(&mut mantissa);
    }
    let binary_exponent = excess as i64 - shift as i64 - divisor_exponent;
    // Exact decimal expansion of mantissa * 2^binary_exponent.
    let (digits, point) = if binary_exponent >= 0 {
        let digits = big_decimal(big_shl(&mantissa, binary_exponent as usize));
        let point = digits.len() as i64;
        (digits, point)
    } else {
        let mut scaled = mantissa;
        let mut remaining = binary_exponent.unsigned_abs();
        while remaining > 0 {
            let step = remaining.min(27);
            big_mul_small(&mut scaled, 5u64.pow(step as u32));
            remaining -= step;
        }
        let digits = big_decimal(scaled);
        let point = digits.len() as i64 + binary_exponent;
        (digits, point)
    };
    let mut decimal = Decimal { digits, point };
    decimal.trim();
    decimal.round(usize::from(precision.max(1)));
    decimal.format_g(negative, usize::from(precision.max(1)))
}

/// math/big's decimal: digits without trailing zeros, value 0.digits * 10^point.
struct Decimal {
    digits: Vec<u8>,
    point: i64,
}

impl Decimal {
    fn trim(&mut self) {
        while self.digits.last() == Some(&b'0') {
            self.digits.pop();
        }
    }

    fn round(&mut self, n: usize) {
        if n >= self.digits.len() {
            return;
        }
        let up = if self.digits[n] == b'5' && n + 1 == self.digits.len() {
            n > 0 && (self.digits[n - 1] - b'0') & 1 != 0
        } else {
            self.digits[n] >= b'5'
        };
        if up {
            let mut at = n;
            while at > 0 && self.digits[at - 1] == b'9' {
                at -= 1;
            }
            if at == 0 {
                self.digits.clear();
                self.digits.push(b'1');
                self.point += 1;
            } else {
                self.digits[at - 1] += 1;
                self.digits.truncate(at);
            }
        } else {
            self.digits.truncate(n);
            self.trim();
        }
    }

    /// math/big ftoa '%g' with an explicit precision.
    fn format_g(&self, negative: bool, mut prec: usize) -> Vec<u8> {
        let mut out = Vec::new();
        if negative {
            out.push(b'-');
        }
        let len = self.digits.len();
        let mut eprec = prec;
        if eprec > len && len as i64 >= self.point {
            eprec = len;
        }
        let exp = self.point - 1;
        if exp < -4 || exp >= eprec as i64 {
            if prec > len {
                prec = len;
            }
            // %e with prec-1 fractional digits.
            out.push(self.digits[0]);
            if prec > 1 {
                out.push(b'.');
                for index in 1..prec {
                    out.push(self.digits.get(index).copied().unwrap_or(b'0'));
                }
            }
            out.push(b'e');
            out.push(if exp < 0 { b'-' } else { b'+' });
            let magnitude = exp.unsigned_abs();
            if magnitude < 10 {
                out.push(b'0');
            }
            push_u64(&mut out, magnitude);
            return out;
        }
        if prec as i64 > self.point {
            prec = len;
        }
        let fraction = (prec as i64 - self.point).max(0) as usize;
        if self.point > 0 {
            let whole = (self.point as usize).min(len);
            out.extend_from_slice(&self.digits[..whole]);
            out.resize(out.len() + self.point as usize - whole, b'0');
        } else {
            out.push(b'0');
        }
        if fraction > 0 {
            out.push(b'.');
            for index in 1..=fraction as i64 {
                let position = self.point + index - 1;
                let digit = usize::try_from(position)
                    .ok()
                    .and_then(|p| self.digits.get(p).copied())
                    .unwrap_or(b'0');
                out.push(digit);
            }
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use arrow_array::ArrayRef;
    use arrow_array::builder::{ListBuilder, StringBuilder};
    use arrow_array::{
        BinaryArray, Date32Array, Date64Array, Decimal128Array, Float32Array, Float64Array,
        Int8Array, Int64Array, StringArray, StructArray, Time32MillisecondArray,
        TimestampMillisecondArray, TimestampNanosecondArray, UInt64Array,
    };
    use arrow_schema::Schema;
    use std::sync::Arc;

    fn batch(columns: Vec<(&str, ArrayRef)>) -> RecordBatch {
        let fields: Vec<Field> = columns
            .iter()
            .map(|(name, array)| Field::new(*name, array.data_type().clone(), true))
            .collect();
        RecordBatch::try_new(
            Arc::new(Schema::new(fields)),
            columns.into_iter().map(|(_, array)| array).collect(),
        )
        .unwrap()
    }

    fn render(batch: &RecordBatch) -> (String, Result<(), RenderError>) {
        let mut out = Vec::new();
        let result = render_batch(batch, &mut out);
        (String::from_utf8_lossy(&out).into_owned(), result)
    }

    #[test]
    fn keys_sort_by_encoded_bytes_and_last_duplicate_wins() {
        let b = batch(vec![
            ("b", Arc::new(Int64Array::from(vec![1])) as ArrayRef),
            ("a", Arc::new(Int64Array::from(vec![2]))),
            ("a!", Arc::new(Int64Array::from(vec![3]))),
            ("b", Arc::new(Int64Array::from(vec![4]))),
            ("a<", Arc::new(Int64Array::from(vec![5]))),
            ("aZ", Arc::new(Int64Array::from(vec![6]))),
        ]);
        // `"a!",` < `"a",` because '!' < '"'; `"a<"` sorts after `"aZ"`.
        assert_eq!(
            render(&b),
            (
                r#"{"a!":3,"a":2,"aZ":6,"a\u003c":5,"b":4}"#.to_owned() + "\n",
                Ok(())
            )
        );
    }

    #[test]
    fn integers_keep_full_width_and_zero_rows_emit_nothing() {
        let b = batch(vec![
            (
                "i",
                Arc::new(Int64Array::from(vec![Some(i64::MIN), None])) as ArrayRef,
            ),
            (
                "u",
                Arc::new(UInt64Array::from(vec![Some(u64::MAX), Some(0)])),
            ),
            ("s", Arc::new(Int8Array::from(vec![-128, 127]))),
        ]);
        assert_eq!(
            render(&b).0,
            "{\"i\":-9223372036854775808,\"s\":-128,\"u\":18446744073709551615}\n{\"i\":null,\"s\":127,\"u\":0}\n"
        );
        assert_eq!(render(&b.slice(0, 0)), (String::new(), Ok(())));
    }

    #[test]
    fn floats_follow_goccy_strconv_layout() {
        let f64s = Float64Array::from(vec![
            0.0,
            -0.0,
            1.5,
            1e20,
            1e21,
            1e-6,
            1e-7,
            5e-324,
            123456789.125,
            0.1,
        ]);
        let mut out = Vec::new();
        for value in f64s.values() {
            push_f64(&mut out, *value).ok().unwrap();
            out.push(b' ');
        }
        assert_eq!(
            String::from_utf8(out).unwrap(),
            "0 -0 1.5 100000000000000000000 1e+21 0.000001 1e-07 5e-324 123456789.125 0.1 "
        );
        let mut cx = Cx {
            scopes: Vec::new(),
            depth: 0,
            raw: None,
        };
        let mut out = Vec::new();
        for value in [
            0.1f32,
            16777217.0,
            1e-7,
            3.4e38,
            f32::NAN,
            f32::NEG_INFINITY,
        ] {
            push_f32(&mut out, value, &mut cx);
            out.push(b' ');
        }
        assert_eq!(
            String::from_utf8(out).unwrap(),
            "0.1 16777216 1e-07 3.4e+38 NaN -Inf "
        );
    }

    #[test]
    fn shortest_float_ties_round_to_even_like_go() {
        // Review-wave V1 vectors; Go's bytes, while core `{:e}` rounds up.
        let mut out = Vec::new();
        for bits in [0x431d_8d54_407f_74e1_u64, 0xc248_04fc_47d0_9200] {
            push_f64(&mut out, f64::from_bits(bits)).ok().unwrap();
            out.push(b' ');
        }
        let mut cx = Cx {
            scopes: Vec::new(),
            depth: 0,
            raw: None,
        };
        push_f32(&mut out, f32::from_bits(0x4729_d710), &mut cx);
        out.push(b' ');
        push_f32(&mut out, go_f16_to_f32(0x0c00), &mut cx);
        assert_eq!(
            String::from_utf8(out).unwrap(),
            "2079541830868280.2 -206325714849.14062 43479.062 0.00024414062"
        );
        assert!(cx.raw.is_none());
    }

    /// Exhaustive cross-check against Go-generated float vectors (review-wave
    /// `vectors.tsv`: `kind<TAB>hex bits<TAB>hex of Go's goccy bytes`). It is
    /// not part of the corpus because the file is 3.6M lines; run with
    /// PB_VALUES_FLOAT_VECTORS=<file> cargo test -- --ignored float_vectors.
    #[test]
    #[ignore = "needs PB_VALUES_FLOAT_VECTORS from the review-wave Go generator"]
    fn go_float_vectors_match_every_bit_pattern() {
        let path = std::env::var_os("PB_VALUES_FLOAT_VECTORS").expect("vector file");
        let text = std::fs::read_to_string(path).unwrap();
        let hex = |s: &str| -> Vec<u8> {
            (0..s.len())
                .step_by(2)
                .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
                .collect()
        };
        let mut cx = Cx {
            scopes: Vec::new(),
            depth: 0,
            raw: None,
        };
        let (mut checked, mut mismatches) = ([0usize; 3], Vec::new());
        for line in text.lines() {
            let mut parts = line.split('\t');
            let (kind, bits, go) = (
                parts.next().unwrap(),
                parts.next().unwrap(),
                parts.next().unwrap(),
            );
            let mut out = Vec::new();
            let index = match kind {
                "f64" => {
                    let value = f64::from_bits(u64::from_str_radix(bits, 16).unwrap());
                    if push_f64(&mut out, value).is_err() {
                        continue;
                    }
                    0
                }
                "f32" => {
                    push_f32(
                        &mut out,
                        f32::from_bits(u32::from_str_radix(bits, 16).unwrap()),
                        &mut cx,
                    );
                    1
                }
                "f16" => {
                    push_f32(
                        &mut out,
                        go_f16_to_f32(u16::from_str_radix(bits, 16).unwrap()),
                        &mut cx,
                    );
                    2
                }
                _ => continue,
            };
            checked[index] += 1;
            if out != hex(go) && mismatches.len() < 20 {
                mismatches.push(format!(
                    "{kind} {bits}: go {:?} rust {:?}",
                    String::from_utf8_lossy(&hex(go)),
                    String::from_utf8_lossy(&out)
                ));
            }
        }
        eprintln!(
            "checked f64={} f32={} f16={}",
            checked[0], checked[1], checked[2]
        );
        assert!(checked.iter().all(|&n| n > 0));
        assert!(mismatches.is_empty(), "{}", mismatches.join("\n"));
    }

    #[test]
    fn strings_escape_like_goccy_normalized_html() {
        let mut out = Vec::new();
        write_str(&mut out, b"<a&b>\"\\\n\r\t\x00\x08\x0c\x1f\x7f\xe2\x80\xa8\xe2\x80\xa9\xff\xe2\x82\xed\xa0\x80\xc0\xaf\xf0\x9f\x98\x80/");
        assert_eq!(
            String::from_utf8(out).unwrap(),
            "\"\\u003ca\\u0026b\\u003e\\\"\\\\\\n\\r\\t\\u0000\\u0008\\u000c\\u001f\x7f\\u2028\\u2029\\ufffd\\ufffd\\ufffd\\ufffd\\ufffd\\ufffd\\ufffd\\ufffd😀/\""
        );
    }

    #[test]
    fn float64_error_truncates_after_complete_rows() {
        let b = batch(vec![(
            "x",
            Arc::new(Float64Array::from(vec![1.0, f64::NAN, 2.0])) as ArrayRef,
        )]);
        assert_eq!(
            render(&b),
            (
                "{\"x\":1}\n".to_owned(),
                Err(RenderError::Encode("json: unsupported value: NaN".into()))
            )
        );
        let b = batch(vec![(
            "x",
            Arc::new(Float32Array::from(vec![f32::INFINITY])) as ArrayRef,
        )]);
        assert_eq!(render(&b), ("{\"x\":+Inf}\n".to_owned(), Ok(())));
    }

    #[test]
    fn nested_lists_render_and_nonfinite_inside_lists_panic() {
        let mut builder = ListBuilder::new(StringBuilder::new());
        builder.append_value([Some("<x>"), None]);
        builder.append_null();
        builder.append_value::<[Option<&str>; 0], _>([]);
        let b = batch(vec![("l", Arc::new(builder.finish()) as ArrayRef)]);
        assert_eq!(
            render(&b).0,
            "{\"l\":[\"\\u003cx\\u003e\",null]}\n{\"l\":null}\n{\"l\":[]}\n"
        );

        let values = Arc::new(Float64Array::from(vec![1.0, f64::NAN]));
        let list = arrow_array::ListArray::new(
            Arc::new(Field::new_list_field(DataType::Float64, true)),
            arrow_buffer::OffsetBuffer::from_lengths([0, 2]),
            values,
            None,
        );
        let b = batch(vec![
            (
                "a",
                Arc::new(Float64Array::from(vec![1.0, 2.0])) as ArrayRef,
            ),
            ("z", Arc::new(list)),
        ]);
        let mut out = b"previous".to_vec();
        let result = render_batch(&b, &mut out);
        assert_eq!(out, b"previous");
        assert_eq!(
            result,
            Err(RenderError::Panic(
                "json: error calling MarshalJSON for type *array.Float64: json: unsupported value: NaN".into()
            ))
        );
    }

    #[test]
    fn binary_uses_base64_and_nil_buffers_render_null() {
        let b = batch(vec![(
            "b",
            Arc::new(BinaryArray::from(vec![
                Some(&b"\x00\xff"[..]),
                Some(b""),
                None,
            ])) as ArrayRef,
        )]);
        assert_eq!(
            render(&b).0,
            "{\"b\":\"AP8=\"}\n{\"b\":\"\"}\n{\"b\":null}\n"
        );
        let b = batch(vec![(
            "b",
            Arc::new(BinaryArray::from(vec![Some(&b""[..])])) as ArrayRef,
        )]);
        assert_eq!(render(&b).0, "{\"b\":null}\n");
    }

    #[test]
    fn temporal_values_format_in_utc() {
        let b = batch(vec![
            (
                "d32",
                Arc::new(Date32Array::from(vec![0, -1, 19_000])) as ArrayRef,
            ),
            (
                "d64",
                Arc::new(Date64Array::from(vec![-1, -86_400_001, 86_400_000])),
            ),
            (
                "t",
                Arc::new(Time32MillisecondArray::from(vec![1_500, -1, 86_400_000])),
            ),
            (
                "ts",
                Arc::new(
                    TimestampMillisecondArray::from(vec![0, -1, 1_700_000_000_123])
                        .with_timezone("Asia/Tokyo"),
                ),
            ),
        ]);
        assert_eq!(
            render(&b).0,
            concat!(
                "{\"d32\":\"1970-01-01\",\"d64\":\"1970-01-01\",\"t\":\"00:00:01.5\",\"ts\":\"1970-01-01 00:00:00\"}\n",
                "{\"d32\":\"1969-12-31\",\"d64\":\"1969-12-31\",\"t\":\"23:59:59.999\",\"ts\":\"1969-12-31 23:59:59.999\"}\n",
                "{\"d32\":\"2022-01-08\",\"d64\":\"1970-01-02\",\"t\":\"00:00:00\",\"ts\":\"2023-11-14 22:13:20.123\"}\n",
            )
        );
        let b = batch(vec![(
            "ts",
            Arc::new(TimestampNanosecondArray::from(vec![i64::MIN])) as ArrayRef,
        )]);
        assert_eq!(render(&b).0, "{\"ts\":\"1677-09-21 00:12:43.145224192\"}\n");
    }

    #[test]
    fn decimals_follow_big_float_g_formatting() {
        for (value, precision, scale, expected) in [
            (12345i128, 10u8, 2i8, "123.45"),
            (-1, 10, 2, "-0.01"),
            (0, 10, 2, "0"),
            (100, 10, 2, "1"),
            (1, 5, 6, "1e-06"),
            (123, 3, -2, "1.23e+04"),
            (
                i128::MAX,
                38,
                0,
                "1.7014118346046923173168730371588410573e+38",
            ),
        ] {
            let magnitude = value.unsigned_abs();
            let text = decimal_text(
                value < 0,
                &[magnitude as u64, (magnitude >> 64) as u64],
                precision,
                scale,
            );
            assert_eq!(
                String::from_utf8(text).unwrap(),
                expected,
                "{value} p{precision} s{scale}"
            );
        }
        let array = Decimal128Array::from(vec![Some(12345), None])
            .with_precision_and_scale(10, 2)
            .unwrap();
        let b = batch(vec![("d", Arc::new(array) as ArrayRef)]);
        assert_eq!(render(&b).0, "{\"d\":\"123.45\"}\n{\"d\":null}\n");
    }

    #[test]
    fn structs_sort_fields_and_null_structs_are_null() {
        let inner = StructArray::new(
            Fields::from(vec![
                Field::new("z", DataType::Utf8, true),
                Field::new("a", DataType::Int64, true),
                Field::new("z", DataType::Utf8, true),
            ]),
            vec![
                Arc::new(StringArray::from(vec!["first", "x"])) as ArrayRef,
                Arc::new(Int64Array::from(vec![1, 2])),
                Arc::new(StringArray::from(vec!["last", "y"])),
            ],
            Some(NullBuffer::from(vec![true, false])),
        );
        let b = batch(vec![("s", Arc::new(inner) as ArrayRef)]);
        assert_eq!(
            render(&b).0,
            "{\"s\":{\"a\":1,\"z\":\"last\"}}\n{\"s\":null}\n"
        );
    }

    #[test]
    fn binary_decoded_strings_render_with_the_original_string_plan() {
        let original = Arc::new(Schema::new(vec![
            Field::new("s", DataType::Utf8, true),
            Field::new("l", DataType::new_list(DataType::LargeUtf8, true), true),
        ]));
        let list = arrow_array::ListArray::new(
            Arc::new(Field::new_list_field(DataType::LargeBinary, true)),
            arrow_buffer::OffsetBuffer::from_lengths([2]),
            Arc::new(arrow_array::LargeBinaryArray::from(vec![
                Some(&b"\xff<"[..]),
                Some(b""),
            ])),
            None,
        );
        let decoded = batch(vec![
            (
                "s",
                Arc::new(BinaryArray::from(vec![Some(&b"a\xe2\x82"[..])])) as ArrayRef,
            ),
            ("l", Arc::new(list)),
        ]);
        let mut renderer = BatchRenderer::new(&original).unwrap();
        let mut out = Vec::new();
        renderer.render(&decoded, &mut out).unwrap();
        assert_eq!(
            String::from_utf8(out).unwrap(),
            "{\"l\":[\"\\ufffd\\u003c\",\"\"],\"s\":\"a\\ufffd\\ufffd\"}\n"
        );
    }

    #[test]
    fn large_list_scratch_is_released_after_the_batch() {
        let big = "x".repeat(4 << 20);
        let mut builder = ListBuilder::new(StringBuilder::new());
        builder.append_value([Some(big.as_str())]);
        let burst = batch(vec![("l", Arc::new(builder.finish()) as ArrayRef)]);
        let mut renderer = BatchRenderer::new(&burst.schema()).unwrap();
        let mut out = Vec::new();
        renderer.render(&burst, &mut out).unwrap();
        assert!(out.len() > 4 << 20);
        assert!(renderer.scratch_capacity() <= SCRATCH_RETAIN_BYTES);

        let mut builder = ListBuilder::new(StringBuilder::new());
        builder.append_value([Some("small")]);
        let small = batch(vec![("l", Arc::new(builder.finish()) as ArrayRef)]);
        out.clear();
        renderer.render(&small, &mut out).unwrap();
        assert_eq!(out, b"{\"l\":[\"small\"]}\n");
        assert!(renderer.scratch_capacity() <= SCRATCH_RETAIN_BYTES);

        // A panicking batch also releases what it grew.
        let values = Arc::new(Float64Array::from(
            vec![0.5; 1 << 18]
                .into_iter()
                .chain([f64::NAN])
                .collect::<Vec<_>>(),
        ));
        let list = arrow_array::ListArray::new(
            Arc::new(Field::new_list_field(DataType::Float64, true)),
            arrow_buffer::OffsetBuffer::from_lengths([values.len()]),
            values,
            None,
        );
        let failing = batch(vec![("l", Arc::new(list) as ArrayRef)]);
        let mut renderer = BatchRenderer::new(&failing.schema()).unwrap();
        assert!(matches!(
            renderer.render(&failing, &mut out),
            Err(RenderError::Panic(_))
        ));
        assert!(renderer.scratch_capacity() <= SCRATCH_RETAIN_BYTES);
    }

    /// Differential check against frozen Go output: tests/fixtures/arrow-go-v13
    /// was generated by harness/values-oracle (pinned Arrow v13 + goccy
    /// v0.10.0, go1.25.4). Each `<case>.arrows` stream is replayed the way
    /// tail does it: rows, then the Println LF, and stop at a panic.
    /// PB_VALUES_ORACLE_DIR overrides the corpus directory.
    #[test]
    fn oracle_corpus_matches_go_bytes() {
        let overridden = std::env::var_os("PB_VALUES_ORACLE_DIR");
        let dir = overridden.clone().map_or_else(
            || std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/arrow-go-v13"),
            std::path::PathBuf::from,
        );
        // 34 original cases plus 10 from decoder review r1.
        check_corpus(&dir, overridden.is_none().then_some(44));
    }

    /// Review-wave (values) corpus: tests/fixtures/arrow-go-v13-values-wave,
    /// generated by harness/values-oracle-wave with the same pinned Go. It
    /// covers shortest-float ties (V1) and empty List/LargeList interval
    /// children (V2), and is replayed exactly like the 44-case corpus.
    #[test]
    fn values_wave_corpus_matches_go_bytes() {
        let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/arrow-go-v13-values-wave");
        check_corpus(&dir, Some(9));
    }

    /// Replay every `<case>.arrows` of a Go oracle corpus and compare stdout,
    /// panic text and per-batch encoder errors with Go's frozen output.
    fn check_corpus(dir: &std::path::Path, expected_cases: Option<usize>) {
        let manifest: serde_json::Value =
            serde_json::from_slice(&std::fs::read(dir.join("manifest.json")).unwrap()).unwrap();
        for file in manifest["files"].as_array().unwrap() {
            let bytes = std::fs::read(dir.join(file["name"].as_str().unwrap())).unwrap();
            assert_eq!(Some(bytes.len() as u64), file["bytes"].as_u64(), "{file}");
        }
        let mut cases: Vec<String> = std::fs::read_dir(dir)
            .unwrap()
            .filter_map(|entry| {
                let path = entry.unwrap().path();
                (path.extension()? == "arrows")
                    .then(|| path.file_stem().unwrap().to_string_lossy().into_owned())
            })
            .collect();
        cases.sort();
        let listed: Vec<&str> = manifest["cases"]
            .as_array()
            .unwrap()
            .iter()
            .map(|c| c.as_str().unwrap())
            .collect();
        assert_eq!(cases, listed);
        if let Some(count) = expected_cases {
            assert_eq!(cases.len(), count);
        }
        let mut failures = Vec::new();
        for case in &cases {
            let read = |ext: &str| std::fs::read(dir.join(format!("{case}.{ext}"))).unwrap();
            let file = std::io::Cursor::new(read("arrows"));
            // Test-only: the oracle streams carry Go-accepted invalid UTF-8.
            // Production decodes strings as binary instead (see session).
            let reader = unsafe {
                arrow_ipc::reader::StreamReader::try_new(file, None)
                    .unwrap()
                    .with_skip_validation(true)
            };
            let original = reader.schema();
            let batches: Vec<RecordBatch> = reader.map(Result::unwrap).collect();
            let as_decoded = replay(&original, batches.iter().cloned());
            // The session's validated path delivers strings as binary arrays.
            let as_wire = replay(&original, batches.iter().map(wire_batch));
            for (path, (stdout, panic, errors)) in [("decoded", as_decoded), ("wire", as_wire)] {
                let expected = read("expected");
                let expected_panic = String::from_utf8(read("panic")).unwrap();
                let expected_errors = String::from_utf8(read("errors")).unwrap();
                if stdout != expected
                    || panic != expected_panic
                    || (panic.is_empty() && errors.join("\n") != expected_errors)
                {
                    failures.push(format!(
                        "{case} ({path}):\n  go:   {:?} panic={expected_panic:?} errors={expected_errors:?}\n  rust: {:?} panic={panic:?} errors={:?}",
                        String::from_utf8_lossy(&expected),
                        String::from_utf8_lossy(&stdout),
                        errors.join("\n"),
                    ));
                }
            }
        }
        assert!(
            failures.is_empty(),
            "{} (case, path) mismatches across {} cases:\n{}",
            failures.len(),
            cases.len(),
            failures.join("\n")
        );
    }

    fn replay(
        schema: &SchemaRef,
        batches: impl Iterator<Item = RecordBatch>,
    ) -> (Vec<u8>, String, Vec<String>) {
        let mut renderer = BatchRenderer::new(schema).unwrap();
        let (mut stdout, mut panic, mut errors) = (Vec::new(), String::new(), Vec::new());
        for batch in batches {
            let mut buffer = Vec::new();
            match renderer.render(&batch, &mut buffer) {
                Ok(()) => errors.push(String::new()),
                Err(RenderError::Encode(message)) => errors.push(message),
                Err(RenderError::Panic(message)) => {
                    assert!(buffer.is_empty());
                    panic = message;
                    break;
                }
            }
            stdout.extend_from_slice(&buffer);
            stdout.push(b'\n');
        }
        (stdout, panic, errors)
    }

    /// Utf8 -> Binary and LargeUtf8 -> LargeBinary at every depth, mirroring
    /// the session's wire schema. Binary building validates offsets only.
    fn wire_type(data_type: &DataType) -> DataType {
        let field = |f: &arrow_schema::FieldRef| {
            Arc::new(f.as_ref().clone().with_data_type(wire_type(f.data_type())))
        };
        match data_type {
            DataType::Utf8 => DataType::Binary,
            DataType::LargeUtf8 => DataType::LargeBinary,
            DataType::List(f) => DataType::List(field(f)),
            DataType::LargeList(f) => DataType::LargeList(field(f)),
            DataType::FixedSizeList(f, n) => DataType::FixedSizeList(field(f), *n),
            DataType::Map(f, sorted) => DataType::Map(field(f), *sorted),
            DataType::Struct(fields) => DataType::Struct(fields.iter().map(field).collect()),
            DataType::Union(fields, mode) => {
                DataType::Union(fields.iter().map(|(id, f)| (id, field(f))).collect(), *mode)
            }
            DataType::Dictionary(key, value) => {
                DataType::Dictionary(key.clone(), Box::new(wire_type(value)))
            }
            DataType::RunEndEncoded(ends, values) => {
                DataType::RunEndEncoded(ends.clone(), field(values))
            }
            other => other.clone(),
        }
    }

    fn wire_array(array: &ArrayRef) -> ArrayRef {
        use arrow_array::{
            FixedSizeListArray, LargeBinaryArray, LargeListArray, ListArray, MapArray, StructArray,
        };
        let field = |f: &arrow_schema::FieldRef| {
            Arc::new(f.as_ref().clone().with_data_type(wire_type(f.data_type())))
        };
        match array.data_type() {
            DataType::Utf8 => {
                let (offsets, values, nulls) = array.as_string::<i32>().clone().into_parts();
                Arc::new(BinaryArray::new(offsets, values, nulls))
            }
            DataType::LargeUtf8 => {
                let (offsets, values, nulls) = array.as_string::<i64>().clone().into_parts();
                Arc::new(LargeBinaryArray::new(offsets, values, nulls))
            }
            DataType::List(f) => {
                let (_, offsets, values, nulls) = array.as_list::<i32>().clone().into_parts();
                Arc::new(ListArray::new(
                    field(f),
                    offsets,
                    wire_array(&values),
                    nulls,
                ))
            }
            DataType::LargeList(f) => {
                let (_, offsets, values, nulls) = array.as_list::<i64>().clone().into_parts();
                Arc::new(LargeListArray::new(
                    field(f),
                    offsets,
                    wire_array(&values),
                    nulls,
                ))
            }
            DataType::FixedSizeList(f, n) => {
                let (_, _, values, nulls) = array.as_fixed_size_list().clone().into_parts();
                Arc::new(FixedSizeListArray::new(
                    field(f),
                    *n,
                    wire_array(&values),
                    nulls,
                ))
            }
            DataType::Struct(fields) => {
                let (_, columns, nulls) = array.as_struct().clone().into_parts();
                let fields: Fields = fields.iter().map(field).collect();
                Arc::new(StructArray::new(
                    fields,
                    columns.iter().map(wire_array).collect(),
                    nulls,
                ))
            }
            DataType::Map(f, sorted) => {
                let (_, offsets, entries, nulls, _) = array.as_map().clone().into_parts();
                let entries: ArrayRef = Arc::new(entries);
                let entries = wire_array(&entries).as_struct().clone();
                Arc::new(MapArray::new(field(f), offsets, entries, nulls, *sorted))
            }
            DataType::Union(fields, _) => {
                let (_, type_ids, offsets, children) = array.as_union().clone().into_parts();
                let fields = fields.iter().map(|(id, f)| (id, field(f))).collect();
                Arc::new(
                    UnionArray::try_new(
                        fields,
                        type_ids,
                        offsets,
                        children.iter().map(wire_array).collect(),
                    )
                    .unwrap(),
                )
            }
            DataType::Dictionary(..) => {
                let dictionary = array.as_any_dictionary();
                dictionary.with_values(wire_array(dictionary.values()))
            }
            DataType::RunEndEncoded(..) => {
                let ree = array.as_any_ree();
                ree.with_values(wire_array(ree.values()))
            }
            _ => array.clone(),
        }
    }

    fn wire_batch(batch: &RecordBatch) -> RecordBatch {
        let fields: Vec<Field> = batch
            .schema()
            .fields()
            .iter()
            .map(|f| f.as_ref().clone().with_data_type(wire_type(f.data_type())))
            .collect();
        let columns = batch.columns().iter().map(wire_array).collect();
        RecordBatch::try_new(Arc::new(Schema::new(fields)), columns).unwrap()
    }

    #[test]
    fn unions_render_type_code_and_value_pairs() {
        // Mirrors the p6-schema-union harness stream: Go printed
        // {"choice":[0,0],...} then {"choice":[1,"u-1"],...}.
        let fields = arrow_schema::UnionFields::try_new(
            [0, 1],
            [
                Field::new("i", DataType::Int64, true),
                Field::new("s", DataType::Utf8, true),
            ],
        )
        .unwrap();
        for mode in [UnionMode::Sparse, UnionMode::Dense] {
            let (ints, strings, offsets): (ArrayRef, ArrayRef, _) = match mode {
                UnionMode::Sparse => (
                    Arc::new(Int64Array::from(vec![Some(0), None])),
                    Arc::new(BinaryArray::from(vec![None, Some(&b"u-1"[..])])),
                    None,
                ),
                UnionMode::Dense => (
                    Arc::new(Int64Array::from(vec![0])),
                    Arc::new(BinaryArray::from(vec![&b"u-1"[..]])),
                    Some(vec![0, 0].into()),
                ),
            };
            let wire_fields = arrow_schema::UnionFields::try_new(
                [0, 1],
                [
                    Field::new("i", DataType::Int64, true),
                    Field::new("s", DataType::Binary, true),
                ],
            )
            .unwrap();
            let union = UnionArray::try_new(
                wire_fields,
                vec![0i8, 1].into(),
                offsets,
                vec![ints, strings],
            )
            .unwrap();
            let original = Arc::new(Schema::new(vec![
                Field::new("choice", DataType::Union(fields.clone(), mode), true),
                Field::new("sequence", DataType::Int64, true),
            ]));
            let wire = RecordBatch::try_new(
                Arc::new(Schema::new(vec![
                    Field::new("choice", union.data_type().clone(), true),
                    Field::new("sequence", DataType::Int64, true),
                ])),
                vec![Arc::new(union), Arc::new(Int64Array::from(vec![0, 1]))],
            )
            .unwrap();
            let mut out = Vec::new();
            BatchRenderer::new(&original)
                .unwrap()
                .render(&wire, &mut out)
                .unwrap();
            assert_eq!(
                String::from_utf8(out).unwrap(),
                "{\"choice\":[0,0],\"sequence\":0}\n{\"choice\":[1,\"u-1\"],\"sequence\":1}\n",
                "{mode:?}"
            );
        }
    }

    #[test]
    fn unsupported_view_types_fail_plan_creation() {
        let schema = Arc::new(Schema::new(vec![Field::new("v", DataType::Utf8View, true)]));
        assert!(BatchRenderer::new(&schema).is_err());
    }
}
