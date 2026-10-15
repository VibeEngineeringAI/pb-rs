// Command values-oracle-wave regenerates tests/fixtures/arrow-go-v13-values-wave:
// the review-wave (values, Opus) cases V1 (shortest-float ties) and V2 (empty
// List/LargeList of interval children). For each case it writes an Arrow IPC
// stream and the exact bytes pinned Go tail prints for it (array.RecordToJSON
// into a bytes.Buffer, then fmt.Println, stopping at a panic), the panic text
// and per-batch encoder errors, in the same layout as harness/values-oracle.
// The first eight cases are the reviewer's probe-e2e cases, built the same
// way. It is a test-data generator built with the root module's pinned Arrow
// v13 / goccy v0.10.0; it is not part of the pb product. Run from the
// repository root:
//
//	GOTOOLCHAIN=local GOPROXY=off GOFLAGS=-mod=readonly <go1.25.4>/bin/go run -trimpath \
//	  ./harness/values-oracle-wave tests/fixtures/arrow-go-v13-values-wave
package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"runtime"
	"runtime/debug"
	"sort"
	"strings"

	"github.com/apache/arrow/go/v13/arrow"
	"github.com/apache/arrow/go/v13/arrow/array"
	"github.com/apache/arrow/go/v13/arrow/float16"
	"github.com/apache/arrow/go/v13/arrow/ipc"
	"github.com/apache/arrow/go/v13/arrow/memory"
)

const generator = "harness/values-oracle-wave/main.go"

var mem = memory.NewGoAllocator()

func rec(names []string, cols ...arrow.Array) arrow.Record {
	fields := make([]arrow.Field, len(cols))
	for i, c := range cols {
		fields[i] = arrow.Field{Name: names[i], Type: c.DataType(), Nullable: true}
	}
	return array.NewRecord(arrow.NewSchema(fields, nil), cols, int64(cols[0].Len()))
}

func fromJSON(dt arrow.DataType, s string) arrow.Array {
	a, _, err := array.FromJSON(mem, dt, bytes.NewReader([]byte(s)))
	must(err)
	return a
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

// run writes the stream exactly as the reviewer's probe did, then decodes it
// with Go's IPC reader, so IPC effects (nil zero-length buffers) count.
func run(dir, name string, recs ...arrow.Record) {
	var stream bytes.Buffer
	w := ipc.NewWriter(&stream, ipc.WithSchema(recs[0].Schema()))
	for _, r := range recs {
		must(w.Write(r))
	}
	must(w.Close())
	rd, err := ipc.NewReader(bytes.NewReader(stream.Bytes()))
	must(err)
	var decoded []arrow.Record
	for rd.Next() {
		r := rd.Record()
		r.Retain()
		decoded = append(decoded, r)
	}
	must(rd.Err())
	out, panicMsg, errs := render(decoded)
	must(os.WriteFile(filepath.Join(dir, name+".arrows"), stream.Bytes(), 0o644))
	must(os.WriteFile(filepath.Join(dir, name+".expected"), out, 0o644))
	must(os.WriteFile(filepath.Join(dir, name+".panic"), []byte(panicMsg), 0o644))
	must(os.WriteFile(filepath.Join(dir, name+".errors"), []byte(strings.Join(errs, "\n")), 0o644))
	fmt.Fprintf(os.Stderr, "%s: %d batches panic=%q\n", name, len(decoded), panicMsg)
}

func must(err error) {
	if err != nil {
		panic(err)
	}
}

func sha(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

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
		Generator:   generator,
		GoToolchain: runtime.Version(),
		Modules:     map[string]string{},
		Regenerate:  "GOTOOLCHAIN=local GOPROXY=off GOFLAGS=-mod=readonly <go1.25.4>/bin/go run -trimpath ./harness/values-oracle-wave tests/fixtures/arrow-go-v13-values-wave",
	}
	source, err := os.ReadFile(generator)
	must(err)
	manifest.GeneratorSHA256 = sha(source)
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
		if c, ok := strings.CutSuffix(name, ".arrows"); ok {
			manifest.Cases = append(manifest.Cases, c)
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

func main() {
	dir := os.Args[1]
	must(os.MkdirAll(dir, 0o755))
	mi := arrow.FixedWidthTypes.MonthInterval
	dt := arrow.FixedWidthTypes.DayTimeInterval
	mdn := arrow.FixedWidthTypes.MonthDayNanoInterval
	// V2: list-of-interval whose child array is empty (every list empty or null).
	run(dir, "list_month_interval_all_empty",
		rec([]string{"l"}, fromJSON(arrow.ListOf(mi), `[[],[],null]`)))
	run(dir, "list_daytime_interval_all_empty",
		rec([]string{"l"}, fromJSON(arrow.ListOf(dt), `[[]]`)))
	run(dir, "list_mdn_interval_all_empty",
		rec([]string{"l"}, fromJSON(arrow.ListOf(mdn), `[[]]`)))
	run(dir, "large_list_month_interval_all_empty",
		rec([]string{"l"}, fromJSON(arrow.LargeListOf(mi), `[[]]`)))
	run(dir, "struct_list_month_interval_all_empty",
		rec([]string{"s"}, fromJSON(arrow.StructOf(arrow.Field{Name: "l", Type: arrow.ListOf(mi), Nullable: true}), `[{"l":[]}]`)))
	// Controls: non-empty child keeps [] for the empty element; Int64 is unaffected.
	run(dir, "list_month_interval_mixed_control",
		rec([]string{"l"}, fromJSON(arrow.ListOf(mi), `[[],[{"months":5}]]`)))
	run(dir, "list_int64_all_empty_control",
		rec([]string{"l"}, fromJSON(arrow.ListOf(arrow.PrimitiveTypes.Int64), `[[]]`)))
	// V1: shortest-float ties (exact value halfway between two shortest candidates).
	f64 := array.NewFloat64Builder(mem)
	f64.AppendValues([]float64{2079541830868280.25, 1700000000123456.25, 0.1}, nil)
	f32 := array.NewFloat32Builder(mem)
	f32.AppendValues([]float32{43479.0625, 453943.625, 0.1}, nil)
	f16 := array.NewFloat16Builder(mem)
	f16.AppendValues([]float16.Num{float16.New(0.000244140625), float16.New(0.0166015625), float16.New(1.5)}, nil)
	lf := array.NewListBuilder(mem, arrow.PrimitiveTypes.Float32)
	lf.Append(true)
	lf.ValueBuilder().(*array.Float32Builder).AppendValues([]float32{43479.0625, 1}, nil)
	lf.Append(true)
	lf.Append(true)
	run(dir, "float_shortest_ties",
		rec([]string{"d", "f", "h", "lf"}, f64.NewArray(), f32.NewArray(), f16.NewArray(), lf.NewArray()))

	// More V1 ties, from the review's mismatch examples: a negative f64 tie,
	// a scientific-notation f64 tie, ties inside a struct, and f16 ties inside a list.
	g64 := array.NewFloat64Builder(mem)
	g64.AppendValues([]float64{math.Float64frombits(0xc24804fc47d09200), math.Float64frombits(0x431d8d54407f74e1), 2.5e21, -0.0}, nil)
	g32 := array.NewFloat32Builder(mem)
	g32.AppendValues([]float32{math.Float32frombits(0x4729d710), -453943.625, 3e-7, 1e21}, nil)
	h16 := array.NewFloat16Builder(mem)
	h16.AppendValues([]float16.Num{float16.New(0.000244140625), float16.New(0.0166015625), float16.New(-0.000244140625), float16.New(2)}, nil)
	st, err := array.NewStructArray([]arrow.Array{g64.NewArray(), g32.NewArray()}, []string{"d", "f"})
	must(err)
	halves := h16.NewArray().(*array.Float16)
	lh := array.NewListBuilder(mem, arrow.FixedWidthTypes.Float16)
	for i := 0; i < halves.Len(); i++ {
		lh.Append(true)
		lh.ValueBuilder().(*array.Float16Builder).Append(halves.Value(i))
	}
	run(dir, "float_ties_nested", rec([]string{"s", "lh"}, st, lh.NewArray()))
	writeManifest(dir)
}
