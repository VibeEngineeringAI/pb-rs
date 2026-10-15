// r2 review probe: read a raw IPC stream file with Go arrow v13 and replay
// tail's Read/RecordToJSON/Println loop. No recover: a panic exits 2.
package main

import (
	"bytes"
	"fmt"
	"os"

	"github.com/apache/arrow/go/v13/arrow/array"
	"github.com/apache/arrow/go/v13/arrow/ipc"
)

func main() {
	raw, err := os.ReadFile(os.Args[1])
	if err != nil {
		panic(err)
	}
	reader, err := ipc.NewReader(bytes.NewReader(raw))
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
		if err := array.RecordToJSON(record, &buf); err != nil {
			fmt.Fprintf(os.Stderr, "tojson err: %v\n", err)
		}
		fmt.Println(buf.String())
	}
}
