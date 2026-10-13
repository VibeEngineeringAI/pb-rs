// pb-fixture is a loopback-only, disposable HTTP/Arrow Flight server for the
// process harness. It is not a Parseable implementation or a production service.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/apache/arrow/go/v13/arrow"
	"github.com/apache/arrow/go/v13/arrow/array"
	"github.com/apache/arrow/go/v13/arrow/flight"
	"github.com/apache/arrow/go/v13/arrow/ipc"
	"github.com/apache/arrow/go/v13/arrow/memory"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

type route struct {
	Method       string `json:"method"`
	Path         string `json:"path"`
	Status       int    `json:"status"`
	Body         string `json:"body"`
	DelayMS      int    `json:"delay_ms"`
	ChunkBytes   int    `json:"chunk_bytes"`
	ChunkDelayMS int    `json:"chunk_delay_ms"`
}

type flightPlan struct {
	Rows      int  `json:"rows"`
	BatchRows int  `json:"batch_rows"`
	DelayMS   int  `json:"delay_ms"`
	Hold      bool `json:"hold"`
}

type scenario struct {
	Routes  []route           `json:"routes"`
	Headers map[string]string `json:"headers"`
	Flight  *flightPlan       `json:"flight"`
}

type request struct {
	Protocol   string            `json:"protocol"`
	Method     string            `json:"method"`
	Path       string            `json:"path"`
	Headers    map[string]string `json:"headers"`
	Body       string            `json:"body"`
	TimeUnixNS int64             `json:"time_unix_ns"`
	Unexpected bool              `json:"unexpected"`
}

type fixture struct {
	flight.BaseFlightServer
	plan     scenario
	grpcPort int
	mu       sync.Mutex
	requests []request
}

var contractHeaders = []string{"authorization", "x-api-key", "x-p-tenant", "cookie", "content-type", "accept"}

func (f *fixture) record(r request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.requests = append(f.requests, r)
}

func (f *fixture) authorized(headers map[string]string) bool {
	for k, v := range f.plan.Headers {
		if headers[strings.ToLower(k)] != v {
			return false
		}
	}
	return true
}

func pause(ctx context.Context, ms int) bool {
	if ms <= 0 {
		return ctx.Err() == nil
	}
	t := time.NewTimer(time.Duration(ms) * time.Millisecond)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}

func (f *fixture) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodGet && r.URL.Path == "/__harness/trace" {
		f.mu.Lock()
		defer f.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(f.requests)
		return
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, 8<<20))
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	entry := request{Protocol: "http", Method: r.Method, Path: r.URL.RequestURI(), Body: string(body), Headers: map[string]string{}, TimeUnixNS: time.Now().UnixNano()}
	for _, h := range contractHeaders {
		if value := r.Header.Get(h); value != "" {
			entry.Headers[h] = value
		}
	}
	var selected *route
	for i := range f.plan.Routes {
		if f.plan.Routes[i].Method == r.Method && f.plan.Routes[i].Path == r.URL.Path {
			selected = &f.plan.Routes[i]
			break
		}
	}
	entry.Unexpected = selected == nil
	f.record(entry)
	if !f.authorized(entry.Headers) {
		http.Error(w, "fixture: incorrect authentication", http.StatusUnauthorized)
		return
	}
	if selected == nil {
		http.Error(w, "fixture: unexpected request", http.StatusInternalServerError)
		return
	}
	if !pause(r.Context(), selected.DelayMS) {
		return
	}
	w.Header().Set("Content-Type", "application/json")
	code := selected.Status
	if code == 0 {
		code = http.StatusOK
	}
	w.WriteHeader(code)
	response := []byte(strings.ReplaceAll(selected.Body, "{{grpc_port}}", fmt.Sprint(f.grpcPort)))
	chunk := selected.ChunkBytes
	if chunk <= 0 {
		chunk = len(response) + 1
	}
	for offset := 0; offset < len(response); offset += chunk {
		end := min(offset+chunk, len(response))
		if _, err := w.Write(response[offset:end]); err != nil {
			return
		}
		if selected.ChunkBytes > 0 {
			w.(http.Flusher).Flush()
		}
		if end < len(response) && !pause(r.Context(), selected.ChunkDelayMS) {
			return
		}
	}
}

func (f *fixture) DoGet(ticket *flight.Ticket, stream flight.FlightService_DoGetServer) error {
	headers := map[string]string{}
	md, _ := metadata.FromIncomingContext(stream.Context())
	for _, h := range contractHeaders {
		if values := md.Get(h); len(values) > 0 {
			headers[h] = strings.Join(values, ",")
		}
	}
	f.record(request{Protocol: "flight", Method: "DoGet", Path: "/arrow.flight.protocol.FlightService/DoGet", Headers: headers, Body: string(ticket.Ticket), TimeUnixNS: time.Now().UnixNano(), Unexpected: f.plan.Flight == nil})
	if !f.authorized(headers) {
		return status.Error(codes.Unauthenticated, "fixture: incorrect authentication")
	}
	if f.plan.Flight == nil {
		return status.Error(codes.Unimplemented, "fixture: unexpected Flight request")
	}
	p := f.plan.Flight
	schema := arrow.NewSchema([]arrow.Field{
		{Name: "sequence", Type: arrow.PrimitiveTypes.Int64},
		{Name: "large_integer", Type: arrow.PrimitiveTypes.Int64},
		{Name: "value", Type: arrow.PrimitiveTypes.Float64},
		{Name: "message", Type: arrow.BinaryTypes.String},
		{Name: "optional", Type: arrow.BinaryTypes.String, Nullable: true},
		{Name: "p_timestamp", Type: &arrow.TimestampType{Unit: arrow.Microsecond}},
	}, nil)
	writer := flight.NewRecordWriter(stream, ipc.WithSchema(schema))
	defer writer.Close()
	batchSize := p.BatchRows
	if batchSize <= 0 {
		batchSize = 128
	}
	for offset := 0; offset < p.Rows; offset += batchSize {
		if !pause(stream.Context(), p.DelayMS) {
			return stream.Context().Err()
		}
		builder := array.NewRecordBuilder(memory.DefaultAllocator, schema)
		for row := offset; row < min(offset+batchSize, p.Rows); row++ {
			builder.Field(0).(*array.Int64Builder).Append(int64(row))
			builder.Field(1).(*array.Int64Builder).Append(9007199254740993 + int64(row))
			builder.Field(2).(*array.Float64Builder).Append(float64(row) / 4)
			builder.Field(3).(*array.StringBuilder).Append("fixture <event> café 日志")
			builder.Field(4).AppendNull()
			builder.Field(5).(*array.TimestampBuilder).Append(arrow.Timestamp(1704067200000000 + row))
		}
		record := builder.NewRecord()
		builder.Release()
		err := writer.Write(record)
		record.Release()
		if err != nil {
			return err
		}
	}
	if p.Hold {
		<-stream.Context().Done()
		return stream.Context().Err()
	}
	// EOF would make pb reconnect forever. A deliberate terminal status permits
	// a finite, repeatable throughput and compatibility case (expected exit 1).
	return status.Error(codes.InvalidArgument, "fixture stream complete")
}

func run() error {
	path := flag.String("scenario", "", "scenario JSON file (required)")
	flag.Parse()
	data, err := os.ReadFile(*path)
	if err != nil {
		return err
	}
	f := &fixture{requests: []request{}}
	if err := json.Unmarshal(data, &f.plan); err != nil {
		return err
	}
	httpListener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return err
	}
	defer httpListener.Close()
	grpcListener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return err
	}
	defer grpcListener.Close()
	f.grpcPort = grpcListener.Addr().(*net.TCPAddr).Port
	server := grpc.NewServer()
	flight.RegisterFlightServiceServer(server, f)
	go func() { _ = server.Serve(grpcListener) }()
	if err := json.NewEncoder(os.Stdout).Encode(map[string]any{"url": "http://" + httpListener.Addr().String(), "grpc_port": f.grpcPort}); err != nil {
		return err
	}
	return http.Serve(httpListener, f)
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
