// Bounded probe of Go arrow v13 tail semantics for negative batch/node lengths.
// Mirrors cmd/tail.go@07af390's established loop: Read, RecordToJSON (error
// ignored), fmt.Println. No recover, so a panic exits 2 as in pb.
package main

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"os"

	"github.com/apache/arrow/go/v13/arrow"
	"github.com/apache/arrow/go/v13/arrow/array"
	"github.com/apache/arrow/go/v13/arrow/ipc"
	"github.com/apache/arrow/go/v13/arrow/memory"
)

const rows = 77

func record(kind string) arrow.Record {
	mem := memory.NewGoAllocator()
	switch kind {
	case "null":
		schema := arrow.NewSchema([]arrow.Field{{Name: "z", Type: arrow.Null, Nullable: true}}, nil)
		return array.NewRecord(schema, []arrow.Array{array.NewNull(rows)}, rows)
	case "struct":
		typ := arrow.StructOf()
		data := array.NewData(typ, rows, []*memory.Buffer{nil}, nil, 0, 0)
		column := array.NewStructData(data)
		schema := arrow.NewSchema([]arrow.Field{{Name: "s", Type: typ, Nullable: true}}, nil)
		return array.NewRecord(schema, []arrow.Array{column}, rows)
	case "int64":
		builder := array.NewInt64Builder(mem)
		for i := 0; i < rows; i++ {
			builder.Append(int64(i + 1000))
		}
		schema := arrow.NewSchema([]arrow.Field{{Name: "n", Type: arrow.PrimitiveTypes.Int64, Nullable: true}}, nil)
		return array.NewRecord(schema, []arrow.Array{builder.NewArray()}, rows)
	}
	panic("unknown kind " + kind)
}

// Replace every little-endian int64 77 (batch length, node length and, for
// Null, node null count) with the given value.
func patch(stream []byte, value int64) (int, []byte) {
	var from, to [8]byte
	binary.LittleEndian.PutUint64(from[:], rows)
	binary.LittleEndian.PutUint64(to[:], uint64(value))
	out := append([]byte(nil), stream...)
	count := 0
	for i := 0; i+8 <= len(out); i++ {
		if bytes.Equal(out[i:i+8], from[:]) {
			copy(out[i:i+8], to[:])
			count++
		}
	}
	return count, out
}

func main() {
	kind, value := os.Args[1], int64(0)
	fmt.Sscan(os.Args[2], &value)
	var stream bytes.Buffer
	rec := record(kind)
	writer := ipc.NewWriter(&stream, ipc.WithSchema(rec.Schema()))
	if err := writer.Write(rec); err != nil {
		panic(err)
	}
	writer.Close()
	count, patched := patch(stream.Bytes(), value)
	fmt.Fprintf(os.Stderr, "patched %d occurrences\n", count)
	reader, err := ipc.NewReader(bytes.NewReader(patched))
	if err != nil {
		fmt.Fprintf(os.Stderr, "init error: %v\n", err)
		os.Exit(1)
	}
	for {
		record, err := reader.Read()
		if err != nil {
			fmt.Fprintf(os.Stderr, "read end: %v\n", err)
			return
		}
		fmt.Fprintf(os.Stderr, "NumRows=%d\n", record.NumRows())
		var buf bytes.Buffer
		array.RecordToJSON(record, &buf)
		fmt.Println(buf.String())
	}
}
