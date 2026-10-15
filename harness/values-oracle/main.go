// Command values-oracle regenerates tests/fixtures/arrow-go-v13: for each case
// an Arrow IPC stream plus the exact bytes pinned Go tail prints for it
// (array.RecordToJSON into a bytes.Buffer, then fmt.Println, stopping at a
// panic), the panic text and per-batch encoder errors. It is a test-data
// generator built with the root module's pinned Arrow v13 / goccy v0.10.0;
// it is not part of the pb product. See docs/phase6-values-notes.md.
//
//	GOTOOLCHAIN=local <go1.25.4>/bin/go run ./harness/values-oracle tests/fixtures/arrow-go-v13
package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"math/big"
	"os"
	"path/filepath"
	"runtime"
	"runtime/debug"
	"sort"
	"strings"

	"github.com/apache/arrow/go/v13/arrow"
	"github.com/apache/arrow/go/v13/arrow/array"
	"github.com/apache/arrow/go/v13/arrow/decimal128"
	"github.com/apache/arrow/go/v13/arrow/decimal256"
	"github.com/apache/arrow/go/v13/arrow/float16"
	"github.com/apache/arrow/go/v13/arrow/ipc"
	"github.com/apache/arrow/go/v13/arrow/memory"
)

var mem = memory.DefaultAllocator

type col struct {
	name string
	arr  arrow.Array
}

func rec(cols ...col) arrow.Record {
	fields := make([]arrow.Field, len(cols))
	arrs := make([]arrow.Array, len(cols))
	n := int64(-1)
	for i, c := range cols {
		fields[i] = arrow.Field{Name: c.name, Type: c.arr.DataType(), Nullable: true}
		arrs[i] = c.arr
		n = int64(c.arr.Len())
	}
	if n < 0 {
		n = 0
	}
	return array.NewRecord(arrow.NewSchema(fields, nil), arrs, n)
}

func fromJSON(dt arrow.DataType, s string) arrow.Array {
	a, _, err := array.FromJSON(mem, dt, strings.NewReader(s))
	if err != nil {
		panic(fmt.Sprintf("FromJSON %s %s: %v", dt, s, err))
	}
	return a
}

func i64s(v ...int64) arrow.Array {
	b := array.NewInt64Builder(mem)
	b.AppendValues(v, nil)
	return b.NewArray()
}

func f64s(v ...float64) arrow.Array {
	b := array.NewFloat64Builder(mem)
	b.AppendValues(v, nil)
	return b.NewArray()
}

func f32s(v ...float32) arrow.Array {
	b := array.NewFloat32Builder(mem)
	b.AppendValues(v, nil)
	return b.NewArray()
}

func strs(v ...string) arrow.Array {
	b := array.NewStringBuilder(mem)
	b.AppendValues(v, nil)
	return b.NewArray()
}

func listOf(child arrow.Array, offsets []int32) arrow.Array {
	ob := array.NewInt32Builder(mem)
	ob.AppendValues(offsets, nil)
	off := ob.NewArray()
	data := array.NewData(arrow.ListOf(child.DataType()), len(offsets)-1,
		[]*memory.Buffer{nil, off.Data().Buffers()[1]}, []arrow.ArrayData{child.Data()}, 0, 0)
	return array.MakeFromData(data)
}

// render mimics cmd/tail.go: buffer per batch, Println, panics stop all output.
func render(recs []arrow.Record) (out []byte, panicMsg string, errs []string) {
	var w bytes.Buffer
	for _, r := range recs {
		var buf bytes.Buffer
		var err error
		func() {
			defer func() {
				if p := recover(); p != nil {
					panicMsg = fmt.Sprint(p)
				}
			}()
			err = array.RecordToJSON(r, &buf)
		}()
		if panicMsg != "" {
			return w.Bytes(), panicMsg, errs
		}
		if err != nil {
			errs = append(errs, err.Error())
		} else {
			errs = append(errs, "")
		}
		fmt.Fprintln(&w, buf.String())
	}
	return w.Bytes(), "", errs
}

func write(dir, name string, recs []arrow.Record) {
	var stream bytes.Buffer
	w := ipc.NewWriter(&stream, ipc.WithSchema(recs[0].Schema()), ipc.WithAllocator(mem))
	for _, r := range recs {
		if err := w.Write(r); err != nil {
			fmt.Fprintf(os.Stderr, "skip %s: write: %v\n", name, err)
			return
		}
	}
	if err := w.Close(); err != nil {
		fmt.Fprintf(os.Stderr, "skip %s: close: %v\n", name, err)
		return
	}
	// Decode the stream as tail would, so dictionary/IPC effects (e.g. nil
	// zero-length buffers) are part of the expectation.
	rd, err := ipc.NewReader(bytes.NewReader(stream.Bytes()), ipc.WithAllocator(mem))
	if err != nil {
		fmt.Fprintf(os.Stderr, "skip %s: read: %v\n", name, err)
		return
	}
	var decoded []arrow.Record
	for rd.Next() {
		r := rd.Record()
		r.Retain()
		decoded = append(decoded, r)
	}
	if rd.Err() != nil {
		fmt.Fprintf(os.Stderr, "skip %s: read: %v\n", name, rd.Err())
		return
	}
	out, panicMsg, errs := render(decoded)
	must(os.WriteFile(filepath.Join(dir, name+".arrows"), stream.Bytes(), 0o644))
	must(os.WriteFile(filepath.Join(dir, name+".expected"), out, 0o644))
	must(os.WriteFile(filepath.Join(dir, name+".panic"), []byte(panicMsg), 0o644))
	must(os.WriteFile(filepath.Join(dir, name+".errors"), []byte(strings.Join(errs, "\n")), 0o644))
	fmt.Fprintf(os.Stderr, "%s: %d batches panic=%q\n", name, len(decoded), panicMsg)
}

func sha(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

// writeManifest records provenance and a digest of every generated file.
func writeManifest(dir string) {
	type file struct {
		Name   string `json:"name"`
		Bytes  int    `json:"bytes"`
		SHA256 string `json:"sha256"`
	}
	manifest := struct {
		Generator       string            `json:"generator"`
		GeneratorSHA256 string            `json:"generator_sha256"`
		GoToolchain     string            `json:"go_toolchain"`
		Modules         map[string]string `json:"modules"`
		Regenerate      string            `json:"regenerate"`
		Cases           []string          `json:"cases"`
		Files           []file            `json:"files"`
	}{
		Generator:   "harness/values-oracle/main.go",
		GoToolchain: runtime.Version(),
		Modules:     map[string]string{},
		Regenerate:  "GOTOOLCHAIN=local <go1.25.4>/bin/go run ./harness/values-oracle tests/fixtures/arrow-go-v13",
	}
	if _, source, _, ok := runtime.Caller(0); ok {
		if data, err := os.ReadFile(source); err == nil {
			manifest.GeneratorSHA256 = sha(data)
		}
	}
	if info, ok := debug.ReadBuildInfo(); ok {
		for _, dep := range info.Deps {
			switch dep.Path {
			case "github.com/apache/arrow/go/v13", "github.com/goccy/go-json":
				manifest.Modules[dep.Path] = dep.Version
			}
		}
	}
	entries, err := os.ReadDir(dir)
	must(err)
	for _, entry := range entries {
		name := entry.Name()
		if name == "manifest.json" {
			continue
		}
		data, err := os.ReadFile(filepath.Join(dir, name))
		must(err)
		manifest.Files = append(manifest.Files, file{Name: name, Bytes: len(data), SHA256: sha(data)})
		if case_, ok := strings.CutSuffix(name, ".arrows"); ok {
			manifest.Cases = append(manifest.Cases, case_)
		}
	}
	sort.Strings(manifest.Cases)
	var out bytes.Buffer
	encoder := json.NewEncoder(&out)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	must(encoder.Encode(manifest))
	must(os.WriteFile(filepath.Join(dir, "manifest.json"), out.Bytes(), 0o644))
}

func must(err error) {
	if err != nil {
		panic(err)
	}
}

func main() {
	dir := os.Args[1]
	must(os.MkdirAll(dir, 0o755))
	nan, inf := math.NaN(), math.Inf(1)

	write(dir, "keys", []arrow.Record{rec(
		col{"b", i64s(1)}, col{"a", i64s(2)}, col{"a!", i64s(3)}, col{"b", i64s(4)},
		col{"a<", i64s(5)}, col{"aZ", i64s(6)}, col{"", i64s(7)}, col{" ", i64s(8)},
		col{" ", i64s(9)}, col{"é", i64s(10)}, col{"a\"", i64s(11)}, col{"a\\", i64s(12)},
	)})

	ints := func() []col {
		b8 := array.NewInt8Builder(mem)
		b8.AppendValues([]int8{math.MinInt8, math.MaxInt8, 0}, []bool{true, true, false})
		b16 := array.NewInt16Builder(mem)
		b16.AppendValues([]int16{math.MinInt16, math.MaxInt16, 0}, nil)
		b32 := array.NewInt32Builder(mem)
		b32.AppendValues([]int32{math.MinInt32, math.MaxInt32, 0}, nil)
		b64 := array.NewInt64Builder(mem)
		b64.AppendValues([]int64{math.MinInt64, math.MaxInt64, 0}, nil)
		u8 := array.NewUint8Builder(mem)
		u8.AppendValues([]uint8{0, math.MaxUint8, 1}, nil)
		u16 := array.NewUint16Builder(mem)
		u16.AppendValues([]uint16{0, math.MaxUint16, 1}, nil)
		u32 := array.NewUint32Builder(mem)
		u32.AppendValues([]uint32{0, math.MaxUint32, 1}, nil)
		u64 := array.NewUint64Builder(mem)
		u64.AppendValues([]uint64{0, math.MaxUint64, 1}, []bool{true, true, false})
		bb := array.NewBooleanBuilder(mem)
		bb.AppendValues([]bool{true, false, false}, []bool{true, true, false})
		return []col{{"i8", b8.NewArray()}, {"i16", b16.NewArray()}, {"i32", b32.NewArray()}, {"i64", b64.NewArray()},
			{"u8", u8.NewArray()}, {"u16", u16.NewArray()}, {"u32", u32.NewArray()}, {"u64", u64.NewArray()},
			{"bool", bb.NewArray()}, {"null", array.NewNull(3)}}
	}
	write(dir, "ints", []arrow.Record{rec(ints()...)})

	write(dir, "floats", []arrow.Record{rec(
		col{"f64", f64s(0, math.Copysign(0, -1), 1, 1.5, 0.1, 1.0/3, 1e20, 1e21, 9.999999999999999e20, 1e-6, 9.99e-7, 1e-7,
			5e-324, math.MaxFloat64, 123456789.125, -2.5e-8, 100, 12345678901234567890)},
		col{"f32", f32s(0, float32(math.Copysign(0, -1)), 1, 1.5, 0.1, 1.0/3, 1e20, 1e21, float32(9.99e20), 1e-6, 9.99e-7, 1e-7,
			1e-45, math.MaxFloat32, 16777217, -2.5e-8, 100, 3.4e38)},
		col{"f16", func() arrow.Array {
			b := array.NewFloat16Builder(mem)
			for _, v := range []float32{0, -0.0, 1, 1.5, 0.1, 1.0 / 3, 65504, 6e-8, 6.1e-5, 0.0001, 2, 3, 0.5, 1e4, -7, 0.25, 100, 1000} {
				b.Append(float16.New(v))
			}
			return b.NewArray()
		}()},
	)})

	f16nan := func() arrow.Array {
		b := array.NewFloat16Builder(mem)
		b.Append(float16.New(float32(nan)))
		b.Append(float16.New(float32(inf)))
		b.Append(float16.New(float32(-inf)))
		return b.NewArray()
	}
	write(dir, "f32_nonfinite_top", []arrow.Record{rec(
		col{"f32", f32s(float32(nan), float32(inf), float32(-inf))}, col{"f16", f16nan()},
	)})

	write(dir, "f64_error_batches", []arrow.Record{
		rec(col{"x", f64s(1, nan, 2)}, col{"a", i64s(1, 2, 3)}),
		rec(col{"x", f64s(3)}, col{"a", i64s(4)}),
		rec(col{"x", f64s()}, col{"a", i64s()}),
		rec(col{"x", f64s(inf)}, col{"a", i64s(5)}),
		rec(col{"x", f64s(-inf, 1)}, col{"a", i64s(6, 7)}),
	})

	write(dir, "strings", []arrow.Record{rec(col{"s", func() arrow.Array {
		b := array.NewStringBuilder(mem)
		for _, v := range []string{"", "plain", "<a&b>", "\"\\/", "\n\r\t\b\f\x00\x01\x1f\x7f",
			"  ", "é😀", "\xff", "a\xffb", "\xe2\x82", "\xe2\x82A", "\xed\xa0\x80", "\xc0\xaf",
			"\xf4\x90\x80\x80", "\xf0\x9f\x98", "12345678<abcdefgh", "abcdefghijk\xff"} {
			b.Append(v)
		}
		b.AppendNull()
		return b.NewArray()
	}()}, col{"ls", func() arrow.Array {
		b := array.NewLargeStringBuilder(mem)
		b.AppendValues([]string{"x", "<", "\xff ", "", "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m", "n"}, nil)
		return b.NewArray()
	}()})})

	bin := func(vals [][]byte, valid []bool) arrow.Array {
		b := array.NewBinaryBuilder(mem, arrow.BinaryTypes.Binary)
		b.AppendValues(vals, valid)
		return b.NewArray()
	}
	write(dir, "binary", []arrow.Record{
		rec(col{"b", bin([][]byte{{0, 0xff}, {}, nil, []byte("hello")}, []bool{true, true, false, true})}),
		rec(col{"b", bin([][]byte{{}, {}}, nil)}),
		rec(col{"b", bin([][]byte{nil, {}}, []bool{false, true})}),
	})
	write(dir, "binary_other", []arrow.Record{rec(
		col{"lb", func() arrow.Array {
			b := array.NewBinaryBuilder(mem, arrow.BinaryTypes.LargeBinary)
			b.AppendValues([][]byte{[]byte("ab"), {}}, nil)
			return b.NewArray()
		}()},
		col{"fsb", func() arrow.Array {
			b := array.NewFixedSizeBinaryBuilder(mem, &arrow.FixedSizeBinaryType{ByteWidth: 3})
			b.AppendValues([][]byte{[]byte("abc"), nil}, []bool{true, false})
			return b.NewArray()
		}()},
	)})

	ts := func(unit arrow.TimeUnit, tz string, v ...int64) arrow.Array {
		b := array.NewTimestampBuilder(mem, &arrow.TimestampType{Unit: unit, TimeZone: tz})
		for _, x := range v {
			b.Append(arrow.Timestamp(x))
		}
		return b.NewArray()
	}
	write(dir, "timestamps", []arrow.Record{rec(
		col{"s", ts(arrow.Second, "", 0, -1, 1700000000, 253402300800, -62135596801, 1e12)},
		col{"ms", ts(arrow.Millisecond, "America/New_York", 0, -1, 1700000000123, 1e13, -1e13, 9223372036854775807)},
		col{"us", ts(arrow.Microsecond, "UTC", 0, -1, 1700000000123456, 1, 1000000, -9223372036854775808)},
		col{"ns", ts(arrow.Nanosecond, "+05:30", 0, -1, 1700000000123456789, math.MinInt64, math.MaxInt64, 10)},
	)})

	write(dir, "dates_times", []arrow.Record{rec(
		col{"d32", func() arrow.Array {
			b := array.NewDate32Builder(mem)
			for _, v := range []int32{0, -1, 19000, math.MaxInt32, math.MinInt32, -719528} {
				b.Append(arrow.Date32(v))
			}
			return b.NewArray()
		}()},
		col{"d64", func() arrow.Array {
			b := array.NewDate64Builder(mem)
			for _, v := range []int64{0, -1, -86400001, 86400000, 1700000000123, -86400000} {
				b.Append(arrow.Date64(v))
			}
			return b.NewArray()
		}()},
		col{"t32s", func() arrow.Array {
			b := array.NewTime32Builder(mem, &arrow.Time32Type{Unit: arrow.Second})
			for _, v := range []int32{0, 1, 86399, 86400, -1, 90061} {
				b.Append(arrow.Time32(v))
			}
			return b.NewArray()
		}()},
		col{"t32ms", func() arrow.Array {
			b := array.NewTime32Builder(mem, &arrow.Time32Type{Unit: arrow.Millisecond})
			for _, v := range []int32{0, 1500, 86399999, 86400000, -1, 10} {
				b.Append(arrow.Time32(v))
			}
			return b.NewArray()
		}()},
		col{"t64us", func() arrow.Array {
			b := array.NewTime64Builder(mem, &arrow.Time64Type{Unit: arrow.Microsecond})
			for _, v := range []int64{0, 1, 86399999999, -1, 9223372036854775807, 120} {
				b.Append(arrow.Time64(v))
			}
			return b.NewArray()
		}()},
		col{"t64ns", func() arrow.Array {
			b := array.NewTime64Builder(mem, &arrow.Time64Type{Unit: arrow.Nanosecond})
			for _, v := range []int64{0, 1, 86399999999999, -1, 100, 123456789} {
				b.Append(arrow.Time64(v))
			}
			return b.NewArray()
		}()},
		col{"dur", func() arrow.Array {
			b := array.NewDurationBuilder(mem, &arrow.DurationType{Unit: arrow.Millisecond})
			for _, v := range []int64{0, -5, 1500, math.MinInt64, math.MaxInt64, 1} {
				b.Append(arrow.Duration(v))
			}
			return b.NewArray()
		}()},
		col{"durns", func() arrow.Array {
			b := array.NewDurationBuilder(mem, &arrow.DurationType{Unit: arrow.Nanosecond})
			b.AppendValues([]arrow.Duration{1, 2, 3, 4, 5, 6}, nil)
			return b.NewArray()
		}()},
	)})

	write(dir, "intervals", []arrow.Record{rec(
		col{"m", func() arrow.Array {
			b := array.NewMonthIntervalBuilder(mem)
			b.AppendValues([]arrow.MonthInterval{0, -3, 14}, []bool{true, true, false})
			return b.NewArray()
		}()},
		col{"dt", func() arrow.Array {
			b := array.NewDayTimeIntervalBuilder(mem)
			b.AppendValues([]arrow.DayTimeInterval{{Days: 1, Milliseconds: -2}, {Days: math.MinInt32, Milliseconds: math.MaxInt32}, {}}, nil)
			return b.NewArray()
		}()},
		col{"mdn", func() arrow.Array {
			b := array.NewMonthDayNanoIntervalBuilder(mem)
			b.AppendValues([]arrow.MonthDayNanoInterval{{Months: 1, Days: 2, Nanoseconds: -3}, {Months: -1, Days: 0, Nanoseconds: math.MaxInt64}, {}}, nil)
			return b.NewArray()
		}()},
	)})

	d128 := func(p, s int32, vals ...string) arrow.Array {
		b := array.NewDecimal128Builder(mem, &arrow.Decimal128Type{Precision: p, Scale: s})
		for _, v := range vals {
			if v == "null" {
				b.AppendNull()
				continue
			}
			n, _ := new(big.Int).SetString(v, 10)
			b.Append(decimal128.FromBigInt(n))
		}
		return b.NewArray()
	}
	d256 := func(p, s int32, vals ...string) arrow.Array {
		b := array.NewDecimal256Builder(mem, &arrow.Decimal256Type{Precision: p, Scale: s})
		for _, v := range vals {
			n, _ := new(big.Int).SetString(v, 10)
			b.Append(decimal256.FromBigInt(n))
		}
		return b.NewArray()
	}
	max38 := "99999999999999999999999999999999999999"
	write(dir, "decimals", []arrow.Record{rec(
		col{"a", d128(10, 2, "12345", "-1", "0", "100", "null", "5", "-99999", "1")},
		col{"b", d128(38, 0, max38, "-"+max38, "1", "10", "123456789012345678901234567890", "0", "7", "170141183460469231731687303715884105727")},
		col{"c", d128(38, 38, max38, "1", "5", "-5", "123", "0", "10000000000000000000000000000000000000", "99")},
		col{"d", d128(5, -2, "123", "1", "99999", "-5", "0", "10", "12345", "100")},
		col{"e", d128(20, 25, "12345678901234567890", "1", "3", "7", "11", "0", "-1", "2")},
		col{"f", d128(3, 1, "999", "995", "994", "-995", "5", "15", "25", "35")},
		col{"g", d128(1, 0, "5", "9", "-9", "0", "1", "2", "3", "4")},
		col{"h", d256(76, 30, "1234567890123456789012345678901234567890123456789012345678901234567890123456",
			"-1", "1", "0", "5", "123", "10", "999999999999999999999999999999")},
	)})

	// Nested values
	listInts := fromJSON(arrow.ListOf(arrow.PrimitiveTypes.Int64), `[[1,2],null,[],[null,9223372036854775807]]`)
	listStrs := listOf(strs("<x>", " ", "a", "\xff"), []int32{0, 2, 2, 3, 4})
	largeList := fromJSON(arrow.LargeListOf(arrow.PrimitiveTypes.Int8), `[[1],[2,3],null,[]]`)
	fsl := fromJSON(arrow.FixedSizeListOf(2, arrow.PrimitiveTypes.Int32), `[[1,2],null,[3,null],[4,5]]`)
	nestedList := fromJSON(arrow.ListOf(arrow.ListOf(arrow.PrimitiveTypes.Int64)), `[[[1],[2,3]],[[]],[null],null]`)
	listF := fromJSON(arrow.ListOf(arrow.PrimitiveTypes.Float64), `[[1.5,1e21],[1e-7],[0],[-0.5]]`)
	listF32 := fromJSON(arrow.ListOf(arrow.PrimitiveTypes.Float32), `[[0.1],[1e21],[1e-7],[3]]`)
	listBinEmpty := listOf(bin([][]byte{{}, {}}, nil), []int32{0, 1, 2, 2, 2})
	write(dir, "lists", []arrow.Record{rec(
		col{"li", listInts}, col{"ls", listStrs}, col{"ll", largeList}, col{"fsl", fsl},
		col{"nl", nestedList}, col{"lf", listF}, col{"lf32", listF32}, col{"lbe", listBinEmpty},
	)})

	inner, err := array.NewStructArray([]arrow.Array{strs("first", "x"), i64s(1, 2), strs("last", "y")}, []string{"z", "a", "z"})
	must(err)
	listOfStruct := listOf(inner, []int32{0, 2})
	structWithList, err := array.NewStructArray([]arrow.Array{listOf(i64s(1, 2, 3), []int32{0, 2, 3}), strs("<", "")}, []string{"l", "b"})
	must(err)
	write(dir, "structs", []arrow.Record{rec(col{"s", inner}, col{"t", structWithList})})
	write(dir, "list_of_struct", []arrow.Record{rec(col{"ls", listOfStruct})})
	write(dir, "struct_null", []arrow.Record{rec(col{"sn", fromJSON(arrow.StructOf(arrow.Field{Name: "q", Type: arrow.PrimitiveTypes.Int64, Nullable: true}), `[{"q":1},null]`)})})

	write(dir, "maps", []arrow.Record{rec(
		col{"m", fromJSON(arrow.MapOf(arrow.BinaryTypes.String, arrow.PrimitiveTypes.Int64), `[[{"key":"b","value":1},{"key":"a","value":null}],null,[]]`)},
	)})

	typeIDs := func(v ...int8) arrow.Array {
		b := array.NewInt8Builder(mem)
		b.AppendValues(v, nil)
		return b.NewArray()
	}
	sparseChild1 := func() arrow.Array {
		b := array.NewInt64Builder(mem)
		b.AppendValues([]int64{1, 0, 0, 0}, []bool{true, false, false, false})
		return b.NewArray()
	}()
	sparse, err := array.NewSparseUnionFromArraysWithFieldCodes(typeIDs(5, 7, 7, 5),
		[]arrow.Array{sparseChild1, strs("", "<s>", "\xff", "")}, []string{"i", "s"}, []arrow.UnionTypeCode{5, 7})
	must(err)
	offsets := func() arrow.Array {
		b := array.NewInt32Builder(mem)
		b.AppendValues([]int32{0, 0, 1, 1}, nil)
		return b.NewArray()
	}()
	dense, err := array.NewDenseUnionFromArraysWithFieldCodes(typeIDs(2, 9, 2, 9), offsets,
		[]arrow.Array{i64s(10, 20), f32s(float32(nan), 2.5)}, []string{"i", "f"}, []arrow.UnionTypeCode{2, 9})
	must(err)
	write(dir, "unions", []arrow.Record{rec(col{"su", sparse}, col{"du", dense})})

	dictType := &arrow.DictionaryType{IndexType: arrow.PrimitiveTypes.Int16, ValueType: arrow.BinaryTypes.String}
	idx := func(v []int16, valid []bool) arrow.Array {
		b := array.NewInt16Builder(mem)
		b.AppendValues(v, valid)
		return b.NewArray()
	}
	dictF := &arrow.DictionaryType{IndexType: arrow.PrimitiveTypes.Int8, ValueType: arrow.PrimitiveTypes.Float64}
	idx8 := func(v ...int8) arrow.Array {
		b := array.NewInt8Builder(mem)
		b.AppendValues(v, nil)
		return b.NewArray()
	}
	write(dir, "dictionaries", []arrow.Record{
		rec(col{"d", array.NewDictionaryArray(dictType, idx([]int16{1, 0, 1, 0}, []bool{true, true, true, false}), strs("a<", "\xffz"))},
			col{"df", array.NewDictionaryArray(dictF, idx8(0, 0, 0, 0), f64s(1.25, nan))}),
		rec(col{"d", array.NewDictionaryArray(dictType, idx([]int16{0}, nil), strs("replaced"))},
			col{"df", array.NewDictionaryArray(dictF, idx8(1), f64s(1.25, nan))}),
	})

	ree := func() arrow.Array {
		b := array.NewInt32Builder(mem)
		b.AppendValues([]int32{2, 3, 5}, nil)
		return array.NewRunEndEncodedArray(b.NewArray(), strs("x", "\xff", "z"), 5, 0)
	}
	write(dir, "ree", []arrow.Record{rec(col{"r", ree()})})

	// Error and panic boundaries
	write(dir, "struct_f64_error", []arrow.Record{rec(col{"s", func() arrow.Array {
		s, err := array.NewStructArray([]arrow.Array{f64s(1, nan)}, []string{"x"})
		must(err)
		return s
	}()})})
	write(dir, "list_nan_panic_first_batch_ok", []arrow.Record{
		rec(col{"l", listOf(f64s(1), []int32{0, 1})}),
		rec(col{"l", listOf(f64s(1, nan), []int32{0, 1, 2})}),
	})
	write(dir, "list_inf_panic", []arrow.Record{rec(col{"l", listOf(f64s(inf), []int32{0, 1})})})
	write(dir, "list_f32_nan_panic", []arrow.Record{rec(col{"l", listOf(f32s(1, float32(nan)), []int32{0, 2})})})
	write(dir, "list_f32_posinf_panic", []arrow.Record{rec(col{"l", listOf(f32s(float32(inf)), []int32{0, 1})})})
	write(dir, "list_f32_neginf_panic", []arrow.Record{rec(col{"l", listOf(f32s(float32(-inf)), []int32{0, 1})})})
	write(dir, "list_struct_f64_panic", []arrow.Record{rec(col{"l", func() arrow.Array {
		s, err := array.NewStructArray([]arrow.Array{f64s(nan)}, []string{"x"})
		must(err)
		return listOf(s, []int32{0, 1})
	}()})})
	write(dir, "list_struct_f32_panic", []arrow.Record{rec(col{"l", func() arrow.Array {
		s, err := array.NewStructArray([]arrow.Array{f32s(float32(nan))}, []string{"x"})
		must(err)
		return listOf(s, []int32{0, 1})
	}()})})
	write(dir, "list_list_f64_panic", []arrow.Record{rec(col{"l", listOf(listOf(f64s(nan), []int32{0, 1}), []int32{0, 1})})})
	write(dir, "panic_beats_error", []arrow.Record{rec(col{"a", f64s(nan)}, col{"z", listOf(f64s(nan), []int32{0, 1})})})
	write(dir, "error_row_before_panic_row", []arrow.Record{rec(col{"a", f64s(1, nan, 1)}, col{"z", listOf(f64s(1, 1, nan), []int32{0, 1, 2, 3})})})
	write(dir, "f16_list_panic", []arrow.Record{rec(col{"l", listOf(f16nan(), []int32{0, 3})})})
	write(dir, "map_f64_panic", []arrow.Record{rec(col{"m", func() arrow.Array {
		b := array.NewMapBuilder(mem, arrow.BinaryTypes.String, arrow.PrimitiveTypes.Float64, false)
		b.Append(true)
		b.KeyBuilder().(*array.StringBuilder).Append("k")
		b.ItemBuilder().(*array.Float64Builder).Append(nan)
		return b.NewArray()
	}()})})
	write(dir, "zero_rows", []arrow.Record{rec(col{"a", i64s()}), rec(col{"a", i64s()})})

	// Review r1: union children use the validity bitmap, not nil-ness.
	sparseOf := func(codes []int8, children []arrow.Array, names []string, fieldCodes []arrow.UnionTypeCode) arrow.Array {
		u, err := array.NewSparseUnionFromArraysWithFieldCodes(typeIDs(codes...), children, names, fieldCodes)
		must(err)
		return u
	}
	nullStrs := func(n int, valid []bool, vals ...string) arrow.Array {
		b := array.NewStringBuilder(mem)
		b.AppendValues(vals, valid)
		return b.NewArray()
	}
	bitmapNull := func() arrow.Array {
		b := array.NewInt64Builder(mem)
		b.AppendValues([]int64{0, 1, 2, 3}, []bool{false, true, true, true})
		return b.NewArray()
	}
	write(dir, "union_child_null_ree_union", []arrow.Record{rec(col{"u", sparseOf(
		[]int8{0, 2, 3, 4},
		[]arrow.Array{
			bitmapNull(),
			array.NewNull(4),
			func() arrow.Array {
				b := array.NewInt32Builder(mem)
				b.AppendValues([]int32{4}, nil)
				return array.NewRunEndEncodedArray(b.NewArray(), nullStrs(1, []bool{false}, ""), 4, 0)
			}(),
			sparseOf([]int8{0, 0, 0, 0}, []arrow.Array{func() arrow.Array {
				b := array.NewInt64Builder(mem)
				b.AppendNulls(4)
				return b.NewArray()
			}()}, []string{"x"}, []arrow.UnionTypeCode{0}),
		},
		[]string{"i", "n", "r", "u"}, []arrow.UnionTypeCode{0, 2, 3, 4})})})
	write(dir, "union_child_dict_null", []arrow.Record{rec(col{"u", sparseOf(
		[]int8{0, 1, 1},
		[]arrow.Array{
			func() arrow.Array {
				b := array.NewInt64Builder(mem)
				b.AppendValues([]int64{0, 0, 0}, []bool{false, true, true})
				return b.NewArray()
			}(),
			array.NewDictionaryArray(&arrow.DictionaryType{IndexType: arrow.PrimitiveTypes.Int8, ValueType: arrow.BinaryTypes.String},
				idx8(0, 0, 1), nullStrs(2, []bool{false, true}, "", "v")),
		},
		[]string{"i", "d"}, []arrow.UnionTypeCode{0, 1})})})

	// Review r1: Go's float16 -> float32 keeps exponent 0, so subnormals shrink.
	write(dir, "f16_bits", []arrow.Record{rec(col{"h", func() arrow.Array {
		b := array.NewUint16Builder(mem)
		b.AppendValues([]uint16{0x0001, 0x03ff, 0x8001, 0x0200, 0x0400, 0x3c00, 0x7bff, 0x8000}, nil)
		raw := b.NewArray()
		return array.MakeFromData(array.NewData(arrow.FixedWidthTypes.Float16, raw.Len(), raw.Data().Buffers(), nil, 0, 0))
	}()})})

	// Review r1: FixedSizeList elements return (not panic) their inner error.
	fslOf := func(child arrow.Array, size int32) arrow.Array {
		data := array.NewData(arrow.FixedSizeListOf(size, child.DataType()), child.Len()/int(size),
			[]*memory.Buffer{nil}, []arrow.ArrayData{child.Data()}, 0, 0)
		return array.MakeFromData(data)
	}
	write(dir, "fsl_top_f64_panic", []arrow.Record{rec(col{"f", fslOf(f64s(1, nan), 2)})})
	write(dir, "list_fsl_f64_panic", []arrow.Record{rec(col{"l", listOf(fslOf(f64s(1, 2, 3, nan), 2), []int32{0, 2})})})
	write(dir, "fsl_fsl_f32_panic", []arrow.Record{rec(col{"f", fslOf(fslOf(f32s(1, float32(nan)), 2), 1)})})
	write(dir, "list_fsl_ok", []arrow.Record{rec(col{"l", listOf(fslOf(f64s(1, 2, 3, 4), 2), []int32{0, 1, 2})})})

	// Review r1: extreme Timestamp(second)/Date64 through Go's absolute-time wrap.
	write(dir, "timestamp_second_extremes", []arrow.Record{rec(
		col{"s", ts(arrow.Second, "UTC", math.MaxInt64, math.MinInt64, -9223372028741760000, -9223372028741760001,
			8113015808, -62135596801, -62135596800)},
		col{"d64", func() arrow.Array {
			b := array.NewDate64Builder(mem)
			for _, v := range []int64{math.MaxInt64, math.MinInt64, 9e15, -9e15, 253402300800000, -62135596800000, -62135596800001} {
				b.Append(arrow.Date64(v))
			}
			return b.NewArray()
		}()},
	)})

	// Review r1: Dictionary.MarshalJSON gathers every element before encoding.
	dictStructs, err := array.NewStructArray([]arrow.Array{f64s(nan, 1), listOf(f64s(1, nan), []int32{0, 1, 2})}, []string{"f", "l"})
	must(err)
	write(dir, "dict_of_list_precedence", []arrow.Record{rec(col{"l", listOf(
		array.NewDictionaryArray(&arrow.DictionaryType{IndexType: arrow.PrimitiveTypes.Int8, ValueType: dictStructs.DataType()},
			idx8(0, 1), dictStructs), []int32{0, 2})})})

	// Lists of unions slice the union; Go slices sparse children with it.
	write(dir, "list_of_unions", []arrow.Record{rec(
		col{"ls", listOf(sparseOf([]int8{5, 7, 7, 5}, []arrow.Array{i64s(1, 2, 3, 4), strs("a", "b", "c", "d")},
			[]string{"i", "s"}, []arrow.UnionTypeCode{5, 7}), []int32{0, 1, 3, 4})},
		col{"ld", listOf(func() arrow.Array {
			b := array.NewInt32Builder(mem)
			b.AppendValues([]int32{0, 0, 1, 1}, nil)
			u, err := array.NewDenseUnionFromArraysWithFieldCodes(typeIDs(2, 9, 2, 9), b.NewArray(),
				[]arrow.Array{i64s(10, 20), strs("x", "y")}, []string{"i", "s"}, []arrow.UnionTypeCode{2, 9})
			must(err)
			return u
		}(), []int32{0, 1, 3, 4})},
	)})
	writeManifest(dir)
}
