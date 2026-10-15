//! Convert IPC body buffers to native byte order before arrow-rs sees offsets.
//! Flatbuffer metadata and compression length prefixes are always little endian.
use arrow_ipc::{self as ipc, MetadataVersion};
use arrow_schema::{DataType, IntervalUnit, UnionMode};
use std::io::Read;

pub(super) fn normalize(
    message: ipc::Message<'_>,
    record: ipc::RecordBatch<'_>,
    types: &[&DataType],
    input: &[u8],
) -> Result<(Vec<u8>, Vec<u8>), String> {
    let mut widths = Vec::new();
    let mut nodes = record.nodes().map_or(0, |nodes| nodes.len());
    for ty in types {
        if !layout(ty, message.version(), &mut nodes, &mut widths)? {
            break;
        }
    }
    let buffers = record.buffers().ok_or("missing IPC buffers")?;
    if buffers.len() < widths.len() {
        return Err("not enough IPC buffers".into());
    }
    let mut body = Vec::new();
    let mut descriptors = Vec::new();
    for (i, buffer) in buffers.iter().take(widths.len()).enumerate() {
        let start = usize::try_from(buffer.offset()).map_err(|_| "negative buffer offset")?;
        let size = usize::try_from(buffer.length()).map_err(|_| "negative buffer length")?;
        let end = start.checked_add(size).ok_or("buffer range overflow")?;
        let encoded = input.get(start..end).ok_or("buffer outside body")?;
        let mut bytes =
            if let Some(compression) = record.compression().filter(|_| !encoded.is_empty()) {
                let prefix: [u8; 8] = encoded
                    .get(..8)
                    .ok_or("short compressed buffer")?
                    .try_into()
                    .unwrap();
                let expected = i64::from_le_bytes(prefix);
                if expected == -1 {
                    encoded[8..].to_vec()
                } else if expected == 0 {
                    // Both Go and Arrow ignore payload bytes for declared zero.
                    Vec::new()
                } else {
                    let expected =
                        usize::try_from(expected).map_err(|_| "invalid decompressed length")?;
                    // Structural validation has already bounded impossible expansion.
                    let reader: Box<dyn Read + '_> = match compression.codec() {
                        ipc::CompressionType::LZ4_FRAME => {
                            Box::new(lz4_flex::frame::FrameDecoder::new(&encoded[8..]))
                        }
                        ipc::CompressionType::ZSTD => Box::new(
                            zstd::stream::read::Decoder::new(&encoded[8..])
                                .map_err(|e| e.to_string())?,
                        ),
                        _ => return Err("unsupported compression codec".into()),
                    };
                    let mut decoded = Vec::new();
                    reader
                        .take(expected.saturating_add(1) as u64)
                        .read_to_end(&mut decoded)
                        .map_err(|e| e.to_string())?;
                    // Deliberately reject mismatched expansion rather than let
                    // malformed frames allocate or expose arbitrary output.
                    if decoded.len() != expected {
                        return Err("incorrect decompressed buffer length".into());
                    }
                    decoded
                }
            } else {
                encoded.to_vec()
            };
        if let Some(pattern) = widths.get(i) {
            swap(&mut bytes, pattern)?;
        }
        // Keep every buffer aligned, including union offsets.
        body.resize(body.len().next_multiple_of(8), 0);
        descriptors.push(ipc::Buffer::new(body.len() as i64, bytes.len() as i64));
        body.extend_from_slice(&bytes);
    }
    let mut builder = flatbuffers::FlatBufferBuilder::new();
    let nodes = record
        .nodes()
        .map(|n| builder.create_vector(&n.iter().copied().collect::<Vec<_>>()));
    let buffers = Some(builder.create_vector(&descriptors));
    let variadic = record
        .variadicBufferCounts()
        .map(|v| builder.create_vector(&v.iter().collect::<Vec<_>>()));
    let record = ipc::RecordBatch::create(
        &mut builder,
        &ipc::RecordBatchArgs {
            length: record.length(),
            nodes,
            buffers,
            variadicBufferCounts: variadic,
            ..Default::default()
        },
    );
    let header = if let Some(dict) = message.header_as_dictionary_batch() {
        ipc::DictionaryBatch::create(
            &mut builder,
            &ipc::DictionaryBatchArgs {
                id: dict.id(),
                data: Some(record),
                isDelta: dict.isDelta(),
            },
        )
        .as_union_value()
    } else {
        record.as_union_value()
    };
    let msg = ipc::Message::create(
        &mut builder,
        &ipc::MessageArgs {
            version: message.version(),
            header_type: message.header_type(),
            header: Some(header),
            bodyLength: body.len() as i64,
            ..Default::default()
        },
    );
    builder.finish(msg, None);
    Ok((builder.finished_data().to_vec(), body))
}

fn swap(bytes: &mut [u8], pattern: &[usize]) -> Result<(), String> {
    let width: usize = pattern.iter().sum();
    if width <= 1 {
        return Ok(());
    }
    // Go swaps complete elements and leaves an incomplete final element alone.
    for element in bytes.chunks_exact_mut(width) {
        let mut offset = 0;
        for &part in pattern {
            element[offset..offset + part].reverse();
            offset += part;
        }
    }
    Ok(())
}

fn layout(
    ty: &DataType,
    version: MetadataVersion,
    nodes: &mut usize,
    out: &mut Vec<Vec<usize>>,
) -> Result<bool, String> {
    if *nodes == 0 {
        return Ok(false);
    }
    *nodes -= 1;
    use DataType::*;
    let mut buffer = |width| out.push(vec![width]);
    match ty {
        Null => (),
        Dictionary(key, _) => {
            buffer(1);
            buffer(key.primitive_width().ok_or("invalid dictionary key type")?);
        }
        Utf8 | Binary | LargeUtf8 | LargeBinary => {
            buffer(1);
            buffer(if matches!(ty, LargeUtf8 | LargeBinary) {
                8
            } else {
                4
            });
            buffer(1);
        }
        List(child) | Map(child, _) | LargeList(child) => {
            buffer(1);
            buffer(if matches!(ty, LargeList(_)) { 8 } else { 4 });
            if !layout(child.data_type(), version, nodes, out)? {
                return Ok(false);
            }
        }
        FixedSizeList(child, _) => {
            buffer(1);
            if !layout(child.data_type(), version, nodes, out)? {
                return Ok(false);
            }
        }
        Struct(fields) => {
            buffer(1);
            for field in fields {
                if !layout(field.data_type(), version, nodes, out)? {
                    return Ok(false);
                }
            }
        }
        Union(fields, mode) => {
            if version < MetadataVersion::V5 {
                buffer(1);
            }
            buffer(1);
            if *mode == UnionMode::Dense {
                buffer(4);
            }
            for (_, field) in fields.iter() {
                if !layout(field.data_type(), version, nodes, out)? {
                    return Ok(false);
                }
            }
        }
        RunEndEncoded(ends, values) => {
            if !layout(ends.data_type(), version, nodes, out)? {
                return Ok(false);
            }
            if !layout(values.data_type(), version, nodes, out)? {
                return Ok(false);
            }
        }
        Interval(IntervalUnit::DayTime) => {
            buffer(1);
            buffer(4);
        }
        Interval(IntervalUnit::MonthDayNano) => {
            buffer(1);
            out.push(vec![4, 4, 8]);
        }
        Boolean | FixedSizeBinary(_) => {
            buffer(1);
            buffer(1);
        }
        Utf8View | BinaryView | ListView(_) | LargeListView(_) => {
            return Err("unsupported IPC view type".into());
        }
        _ => {
            buffer(1);
            buffer(
                ty.primitive_width()
                    .ok_or_else(|| format!("unsupported IPC type {ty}"))?,
            );
        }
    }
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::super::{Decoded, Decoder, NATIVE_ENDIAN, copy_field};
    use super::*;
    use arrow_array::{
        Array, ArrayRef, Decimal128Array, Decimal256Array, DictionaryArray, Int64Array, ListArray,
        RecordBatch, StringArray, types::Int32Type, types::Int64Type,
    };
    use arrow_flight::FlightData;
    use arrow_ipc::writer::{
        DictionaryTracker, IpcDataGenerator, IpcWriteContext, IpcWriteOptions,
    };
    use arrow_schema::{Field, Schema};
    use std::io::Write;
    use std::sync::Arc;

    fn fixture() -> RecordBatch {
        let list = ListArray::from_iter_primitive::<Int64Type, _, _>(vec![
            Some(vec![Some(0x102030405060708), None]),
            None,
            Some(vec![Some(-29)]),
        ]);
        let dictionary: DictionaryArray<Int32Type> = vec![Some("north"), None, Some("south")]
            .into_iter()
            .collect();
        let arrays: Vec<ArrayRef> = vec![
            Arc::new(Int64Array::from(vec![
                Some(0x102030405060708),
                None,
                Some(-42),
            ])),
            Arc::new(StringArray::from(vec![Some("abc"), None, Some("é")])),
            Arc::new(list),
            Arc::new(dictionary),
            Arc::new(
                Decimal128Array::from(vec![Some(123456789012345678901_i128), None, Some(-123)])
                    .with_precision_and_scale(30, 3)
                    .unwrap(),
            ),
            Arc::new(
                Decimal256Array::from(vec![
                    Some(arrow_buffer::i256::from_i128(123456789012345678901)),
                    None,
                    Some(arrow_buffer::i256::from_i128(-123)),
                ])
                .with_precision_and_scale(60, 3)
                .unwrap(),
            ),
        ];
        let schema = Arc::new(Schema::new(
            arrays
                .iter()
                .enumerate()
                .map(|(i, a)| Field::new(format!("f{i}"), a.data_type().clone(), true))
                .collect::<Vec<_>>(),
        ));
        RecordBatch::try_new(schema, arrays).unwrap()
    }

    fn stream(batch: &RecordBatch, codec: Option<ipc::CompressionType>) -> Vec<FlightData> {
        let options = IpcWriteOptions::default()
            .try_with_compression(codec)
            .unwrap();
        let generator = IpcDataGenerator::default();
        let mut tracker = DictionaryTracker::new(false);
        let schema = generator.schema_to_bytes_with_dictionary_tracker(
            &batch.schema(),
            &mut tracker,
            &options,
        );
        let (dicts, record) = generator
            .encode(
                batch,
                &mut tracker,
                &options,
                &mut IpcWriteContext::default(),
            )
            .unwrap();
        std::iter::once(schema)
            .chain(dicts)
            .chain(std::iter::once(record))
            .map(Into::into)
            .collect()
    }

    fn opposite_schema(data: &mut FlightData) {
        let message = ipc::root_as_message(&data.data_header).unwrap();
        let schema = message.header_as_schema().unwrap();
        let mut builder = flatbuffers::FlatBufferBuilder::new();
        let fields = schema
            .fields()
            .unwrap()
            .iter()
            .map(|f| copy_field(&mut builder, f).unwrap())
            .collect::<Vec<_>>();
        let fields = builder.create_vector(&fields);
        let schema = ipc::Schema::create(
            &mut builder,
            &ipc::SchemaArgs {
                endianness: if NATIVE_ENDIAN == ipc::Endianness::Little {
                    ipc::Endianness::Big
                } else {
                    ipc::Endianness::Little
                },
                fields: Some(fields),
                ..Default::default()
            },
        );
        let message = ipc::Message::create(
            &mut builder,
            &ipc::MessageArgs {
                version: message.version(),
                header_type: ipc::MessageHeader::Schema,
                header: Some(schema.as_union_value()),
                ..Default::default()
            },
        );
        builder.finish(message, None);
        data.data_header = builder.finished_data().to_vec().into();
    }

    // Fixture-specific wire conversion is independent of the implementation's
    // recursive type layout and normalization code.
    fn opposite_body(data: &mut FlightData) {
        let message = ipc::root_as_message(&data.data_header).unwrap();
        let (record, widths): (_, &[usize]) = if let Some(d) = message.header_as_dictionary_batch()
        {
            (d.data().unwrap(), &[1, 4, 1])
        } else {
            (
                message.header_as_record_batch().unwrap(),
                &[1, 8, 1, 4, 1, 1, 4, 1, 8, 1, 4, 1, 16, 1, 32],
            )
        };
        let buffers = record.buffers().unwrap();
        assert_eq!(buffers.len(), widths.len());
        let mut body = data.data_body.to_vec();
        for (buffer, width) in buffers.iter().zip(widths) {
            let bytes =
                &mut body[buffer.offset() as usize..(buffer.offset() + buffer.length()) as usize];
            assert_eq!(bytes.len() % width, 0);
            for value in bytes.chunks_exact_mut(*width) {
                value.reverse();
            }
        }
        data.data_body = body.into();
    }

    // Rebuild a swapped message with independently encoded compressed buffers.
    fn compress(data: &mut FlightData, codec: ipc::CompressionType) {
        let message = ipc::root_as_message(&data.data_header).unwrap();
        let dictionary = message.header_as_dictionary_batch();
        let record = dictionary
            .and_then(|d| d.data())
            .or_else(|| message.header_as_record_batch())
            .unwrap();
        let mut body = Vec::new();
        let mut buffers = Vec::new();
        for buffer in record.buffers().unwrap() {
            let bytes = &data.data_body
                [buffer.offset() as usize..(buffer.offset() + buffer.length()) as usize];
            body.resize(body.len().next_multiple_of(8), 0);
            let offset = body.len();
            if !bytes.is_empty() {
                body.extend_from_slice(&(bytes.len() as i64).to_le_bytes());
                if codec == ipc::CompressionType::ZSTD {
                    body.extend(zstd::stream::encode_all(bytes, 1).unwrap());
                } else {
                    let mut encoder = lz4_flex::frame::FrameEncoder::new(Vec::new());
                    encoder.write_all(bytes).unwrap();
                    body.extend(encoder.finish().unwrap());
                }
            }
            buffers.push(ipc::Buffer::new(
                offset as i64,
                (body.len() - offset) as i64,
            ));
        }
        let mut b = flatbuffers::FlatBufferBuilder::new();
        let nodes = b.create_vector(&record.nodes().unwrap().iter().copied().collect::<Vec<_>>());
        let buffers = b.create_vector(&buffers);
        let compression = ipc::BodyCompression::create(
            &mut b,
            &ipc::BodyCompressionArgs {
                codec,
                method: ipc::BodyCompressionMethod::BUFFER,
            },
        );
        let record = ipc::RecordBatch::create(
            &mut b,
            &ipc::RecordBatchArgs {
                length: record.length(),
                nodes: Some(nodes),
                buffers: Some(buffers),
                compression: Some(compression),
                ..Default::default()
            },
        );
        let header = if let Some(d) = dictionary {
            ipc::DictionaryBatch::create(
                &mut b,
                &ipc::DictionaryBatchArgs {
                    id: d.id(),
                    data: Some(record),
                    isDelta: d.isDelta(),
                },
            )
            .as_union_value()
        } else {
            record.as_union_value()
        };
        let msg = ipc::Message::create(
            &mut b,
            &ipc::MessageArgs {
                version: message.version(),
                header_type: message.header_type(),
                header: Some(header),
                bodyLength: body.len() as i64,
                ..Default::default()
            },
        );
        b.finish(msg, None);
        data.data_header = b.finished_data().to_vec().into();
        data.data_body = body.into();
    }

    #[test]
    fn native_and_opposite_flight_nested_dictionary_compression() {
        let expected = fixture();
        for codec in [
            None,
            Some(ipc::CompressionType::LZ4_FRAME),
            Some(ipc::CompressionType::ZSTD),
        ] {
            for opposite in [false, true] {
                let mut messages = stream(&expected, if opposite { None } else { codec });
                if opposite {
                    opposite_schema(&mut messages[0]);
                    for data in &mut messages[1..] {
                        opposite_body(data);
                        if let Some(codec) = codec {
                            compress(data, codec);
                        }
                    }
                }
                let mut decoder = Decoder::new(&messages[0].data_header).unwrap();
                let mut batches = Vec::new();
                for data in messages.into_iter().skip(1) {
                    if let Decoded::Batch(batch) = decoder.decode(data).unwrap() {
                        batches.push(batch);
                    }
                }
                let mut renderer = super::super::BatchRenderer::new(&expected.schema()).unwrap();
                let mut actual = Vec::new();
                for batch in &batches {
                    renderer.render(batch, &mut actual).unwrap();
                }
                let mut wanted = Vec::new();
                renderer.render(&expected, &mut wanted).unwrap();
                assert_eq!(batches.len(), 1);
                assert_eq!(actual, wanted, "codec={codec:?}, opposite={opposite}");
            }
        }
    }

    #[test]
    fn opposite_dictionary_replacements_and_deltas() {
        use arrow_array::Int32Array;
        use arrow_ipc::writer::DictionaryHandling;
        for handling in [DictionaryHandling::Resend, DictionaryHandling::Delta] {
            for codec in [
                None,
                Some(ipc::CompressionType::LZ4_FRAME),
                Some(ipc::CompressionType::ZSTD),
            ] {
                let options = IpcWriteOptions::default().with_dictionary_handling(handling);
                let generator = IpcDataGenerator::default();
                let mut tracker = DictionaryTracker::new(false);
                let mut context = IpcWriteContext::default();
                let mut expected = Vec::new();
                for (keys, values) in [
                    (vec![0, 1], vec!["a", "b"]),
                    (
                        vec![2, 0],
                        if matches!(handling, DictionaryHandling::Delta) {
                            vec!["a", "b", "c"]
                        } else {
                            vec!["x", "y", "z"]
                        },
                    ),
                ] {
                    let array = DictionaryArray::<Int32Type>::try_new(
                        Int32Array::from(keys),
                        Arc::new(StringArray::from(values)),
                    )
                    .unwrap();
                    let schema = Arc::new(Schema::new(vec![Field::new(
                        "d",
                        array.data_type().clone(),
                        true,
                    )]));
                    expected.push(RecordBatch::try_new(schema, vec![Arc::new(array)]).unwrap());
                }
                let mut schema: FlightData = generator
                    .schema_to_bytes_with_dictionary_tracker(
                        &expected[0].schema(),
                        &mut tracker,
                        &options,
                    )
                    .into();
                opposite_schema(&mut schema);
                let mut decoder = Decoder::new(&schema.data_header).unwrap();
                for expected_batch in expected {
                    let (dictionaries, record) = generator
                        .encode(&expected_batch, &mut tracker, &options, &mut context)
                        .unwrap();
                    assert_eq!(dictionaries.len(), 1);
                    for encoded in dictionaries.into_iter().chain(std::iter::once(record)) {
                        let mut data: FlightData = encoded.into();
                        let message = ipc::root_as_message(&data.data_header).unwrap();
                        let (record, widths): (_, &[usize]) =
                            if let Some(d) = message.header_as_dictionary_batch() {
                                (d.data().unwrap(), &[1, 4, 1])
                            } else {
                                (message.header_as_record_batch().unwrap(), &[1, 4])
                            };
                        let mut body = data.data_body.to_vec();
                        assert_eq!(record.buffers().unwrap().len(), widths.len());
                        for (buffer, width) in record.buffers().unwrap().iter().zip(widths) {
                            for value in body[buffer.offset() as usize
                                ..(buffer.offset() + buffer.length()) as usize]
                                .chunks_exact_mut(*width)
                            {
                                value.reverse();
                            }
                        }
                        data.data_body = body.into();
                        if let Some(codec) = codec {
                            compress(&mut data, codec);
                        }
                        if let Decoded::Batch(actual) = decoder.decode(data).unwrap() {
                            let mut renderer =
                                super::super::BatchRenderer::new(&expected_batch.schema()).unwrap();
                            let (mut got, mut wanted) = (Vec::new(), Vec::new());
                            renderer.render(&actual, &mut got).unwrap();
                            renderer.render(&expected_batch, &mut wanted).unwrap();
                            assert_eq!(got, wanted);
                        }
                    }
                }
            }
        }
    }

    #[test]
    fn opposite_endian_malformed_body_is_rejected_without_panic() {
        for codec in [
            None,
            Some(ipc::CompressionType::LZ4_FRAME),
            Some(ipc::CompressionType::ZSTD),
        ] {
            let batch = fixture();
            let mut messages = stream(&batch, None);
            opposite_schema(&mut messages[0]);
            for data in &mut messages[1..] {
                opposite_body(data);
                if let Some(codec) = codec {
                    compress(data, codec);
                }
            }
            for index in 1..messages.len() {
                for corruption in 0..if codec.is_some() { 3 } else { 1 } {
                    let mut decoder = Decoder::new(&messages[0].data_header).unwrap();
                    for prior in &messages[1..index] {
                        decoder.decode(prior.clone()).unwrap();
                    }
                    let mut data = messages[index].clone();
                    if corruption == 0 {
                        data.data_body = data.data_body.slice(..data.data_body.len() / 2);
                    } else {
                        let message = ipc::root_as_message(&data.data_header).unwrap();
                        let record = message
                            .header_as_record_batch()
                            .or_else(|| message.header_as_dictionary_batch().unwrap().data())
                            .unwrap();
                        let buffer = record
                            .buffers()
                            .unwrap()
                            .iter()
                            .find(|b| b.length() > 8)
                            .unwrap();
                        let start = buffer.offset() as usize;
                        let mut body = data.data_body.to_vec();
                        if corruption == 1 {
                            body[start..start + 8].copy_from_slice(&(-2_i64).to_le_bytes());
                        } else {
                            // A plausible size prefix with an invalid frame magic.
                            body[start + 8..start + 12].fill(0);
                        }
                        data.data_body = body.into();
                    }
                    assert!(decoder.decode(data).is_err());
                }
            }
        }
    }

    fn raw_record(
        length: i64,
        nodes: &[ipc::FieldNode],
        buffers: Option<&[ipc::Buffer]>,
        codec: Option<ipc::CompressionType>,
        dictionary: Option<i64>,
        body: Vec<u8>,
    ) -> FlightData {
        let mut b = flatbuffers::FlatBufferBuilder::new();
        let nodes = b.create_vector(nodes);
        let buffers = buffers.map(|buffers| b.create_vector(buffers));
        let compression = codec.map(|codec| {
            ipc::BodyCompression::create(
                &mut b,
                &ipc::BodyCompressionArgs {
                    codec,
                    method: ipc::BodyCompressionMethod::BUFFER,
                },
            )
        });
        let record = ipc::RecordBatch::create(
            &mut b,
            &ipc::RecordBatchArgs {
                length,
                nodes: Some(nodes),
                buffers,
                compression,
                ..Default::default()
            },
        );
        let header = if let Some(id) = dictionary {
            ipc::DictionaryBatch::create(
                &mut b,
                &ipc::DictionaryBatchArgs {
                    id,
                    data: Some(record),
                    isDelta: false,
                },
            )
            .as_union_value()
        } else {
            record.as_union_value()
        };
        let msg = ipc::Message::create(
            &mut b,
            &ipc::MessageArgs {
                version: MetadataVersion::V5,
                header_type: if dictionary.is_some() {
                    ipc::MessageHeader::DictionaryBatch
                } else {
                    ipc::MessageHeader::RecordBatch
                },
                header: Some(header),
                bodyLength: body.len() as i64,
                ..Default::default()
            },
        );
        b.finish(msg, None);
        FlightData {
            data_header: b.finished_data().to_vec().into(),
            data_body: body.into(),
            ..Default::default()
        }
    }

    #[test]
    fn zero_and_raw_compression_prefixes_match_native_order() {
        for codec in [ipc::CompressionType::ZSTD, ipc::CompressionType::LZ4_FRAME] {
            for opposite in [false, true] {
                let batch = RecordBatch::try_from_iter(vec![(
                    "i",
                    Arc::new(Int64Array::from(vec![42])) as ArrayRef,
                )])
                .unwrap();
                let mut schema = stream(&batch, None).remove(0);
                if opposite {
                    opposite_schema(&mut schema);
                }
                // Empty validity is legal when null_count is zero. Prefix-only
                // and junk trailing payload must both be ignored for size zero.
                for payload in [vec![], b"not a compression frame".to_vec()] {
                    let mut body = 0_i64.to_le_bytes().to_vec();
                    body.extend(payload);
                    let validity_len = body.len();
                    body.resize(body.len().next_multiple_of(8), 0);
                    let values_at = body.len();
                    body.extend((-1_i64).to_le_bytes());
                    let value = if opposite { 42_i64.swap_bytes() } else { 42 };
                    body.extend(value.to_ne_bytes());
                    let data = raw_record(
                        1,
                        &[ipc::FieldNode::new(1, 0)],
                        Some(&[
                            ipc::Buffer::new(0, validity_len as i64),
                            ipc::Buffer::new(values_at as i64, 16),
                        ]),
                        Some(codec),
                        None,
                        body,
                    );
                    let mut decoder = Decoder::new(&schema.data_header).unwrap();
                    let Decoded::Batch(decoded) = decoder.decode(data).unwrap() else {
                        panic!("record")
                    };
                    assert_eq!(
                        decoded
                            .column(0)
                            .as_any()
                            .downcast_ref::<Int64Array>()
                            .unwrap()
                            .value(0),
                        42
                    );
                }
            }
        }
    }

    #[test]
    fn native_and_foreign_error_order_and_unread_layouts_match() {
        for opposite in [false, true] {
            let mut messages = stream(&fixture(), None);
            if opposite {
                opposite_schema(&mut messages[0]);
            }
            // Pending dictionaries win over a malformed record body.
            for length in [0, 3] {
                let malformed = raw_record(
                    length,
                    &[],
                    Some(&[ipc::Buffer::new(0, 10)]),
                    Some(ipc::CompressionType::ZSTD),
                    None,
                    vec![],
                );
                let error = Decoder::new(&messages[0].data_header)
                    .unwrap()
                    .decode(malformed)
                    .err()
                    .unwrap();
                let expected = if length == 0 {
                    "arrow/ipc: IPC stream did not have the expected (1) dictionaries at the start of the stream".to_string()
                } else {
                    "arrow/ipc: no dictionary type found with id: 3".to_string()
                };
                assert_eq!(error, expected);
            }
            // Body bounds win over an unknown dictionary ID.
            let malformed = raw_record(
                1,
                &[],
                Some(&[ipc::Buffer::new(0, 10)]),
                None,
                Some(999),
                vec![],
            );
            assert_eq!(
                Decoder::new(&messages[0].data_header)
                    .unwrap()
                    .decode(malformed)
                    .err()
                    .unwrap(),
                format!("{}EOF", super::super::UNKNOWN_READ)
            );
            // NoRows never touches a missing buffers vector.
            let empty = RecordBatch::try_from_iter(vec![(
                "n",
                Arc::new(arrow_array::NullArray::new(0)) as ArrayRef,
            )])
            .unwrap();
            let mut schema = stream(&empty, None).remove(0);
            if opposite {
                opposite_schema(&mut schema);
            }
            let data = raw_record(-1, &[ipc::FieldNode::new(-1, -1)], None, None, None, vec![]);
            let Decoded::Batch(decoded) = Decoder::new(&schema.data_header)
                .unwrap()
                .decode(data)
                .unwrap()
            else {
                panic!("record")
            };
            assert_eq!(decoded.num_rows(), 0);
        }
    }

    #[test]
    fn ignored_extra_buffers_are_not_decompressed_and_odd_tails_survive() {
        let batch = RecordBatch::try_from_iter(vec![(
            "i",
            Arc::new(Int64Array::from(vec![42])) as ArrayRef,
        )])
        .unwrap();
        let mut schema = stream(&batch, None).remove(0);
        opposite_schema(&mut schema);
        // Complete Int64 value plus unused tail; extra malformed compressed
        // buffer is in bounds but is never read by the schema's one column.
        let mut body = (-1_i64).to_le_bytes().to_vec();
        body.extend(42_i64.swap_bytes().to_ne_bytes());
        body.push(0x5a);
        let data = raw_record(
            1,
            &[ipc::FieldNode::new(1, 0)],
            Some(&[
                ipc::Buffer::new(0, 0),
                ipc::Buffer::new(0, 17),
                ipc::Buffer::new(16, 1),
            ]),
            Some(ipc::CompressionType::ZSTD),
            None,
            body,
        );
        let message = ipc::root_as_message(&data.data_header).unwrap();
        let (header, body) = normalize(
            message,
            message.header_as_record_batch().unwrap(),
            &[&DataType::Int64],
            &data.data_body,
        )
        .unwrap();
        assert_eq!(body, [42_i64.to_ne_bytes().as_slice(), &[0x5a]].concat());
        // A missing next node makes the decoder stop before that column's
        // buffers too; do not decompress a claimed second-column buffer.
        assert!(
            normalize(
                message,
                message.header_as_record_batch().unwrap(),
                &[&DataType::Int64, &DataType::Int64],
                &data.data_body
            )
            .is_ok()
        );
        assert_eq!(
            ipc::root_as_message(&header)
                .unwrap()
                .header_as_record_batch()
                .unwrap()
                .buffers()
                .unwrap()
                .len(),
            2
        );
        let Decoded::Batch(decoded) = Decoder::new(&schema.data_header)
            .unwrap()
            .decode(data)
            .unwrap()
        else {
            panic!("record")
        };
        assert_eq!(
            decoded
                .column(0)
                .as_any()
                .downcast_ref::<Int64Array>()
                .unwrap()
                .value(0),
            42
        );
    }

    #[test]
    fn compound_interval_components_and_partial_elements() {
        let mut bytes = vec![1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16];
        swap(&mut bytes, &[4, 4, 8]).unwrap();
        assert_eq!(
            bytes,
            [4, 3, 2, 1, 8, 7, 6, 5, 16, 15, 14, 13, 12, 11, 10, 9]
        );
        let tail = bytes[0..15].to_vec();
        swap(&mut bytes[..15], &[4, 4, 8]).unwrap();
        assert_eq!(&bytes[..15], tail);
    }
}
