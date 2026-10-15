// pb-fixture is a loopback-only, disposable HTTP/Arrow Flight server for the
// process harness. It is not a Parseable implementation or a production service.
package main

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"strconv"
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

	"golang.org/x/sys/unix"
)

// capabilities lists optional route features; phase3.py checks them before export.
var capabilities = []string{"generate", "gzip", "milestones", "phase6"}

type route struct {
	Method       string `json:"method"`
	Path         string `json:"path"`
	Status       int    `json:"status"`
	Body         string `json:"body"`
	DelayMS      int    `json:"delay_ms"`
	ChunkBytes   int    `json:"chunk_bytes"`
	ChunkDelayMS int    `json:"chunk_delay_ms"`
	// Generate replaces Body with a deterministic expansion (shared with phase3.expand_generate).
	Generate *generate `json:"generate"`
	// Gzip sends Content-Encoding: gzip with the body compressed once at startup.
	Gzip bool `json:"gzip"`

	payload      []byte
	uncompressed int
}

type generate struct {
	Prefix    string `json:"prefix"`
	Item      string `json:"item"`
	Separator string `json:"separator"`
	Suffix    string `json:"suffix"`
	Count     int    `json:"count"`
	Width     int    `json:"width"`
	BlobBytes int    `json:"blob_bytes"`
}

func (g *generate) expand() []byte {
	var out bytes.Buffer
	blob := strings.Repeat("x", max(g.BlobBytes, 0))
	out.WriteString(g.Prefix)
	for i := 0; i < g.Count; i++ {
		if i > 0 {
			out.WriteString(g.Separator)
		}
		number := strconv.Itoa(i)
		item := strings.ReplaceAll(g.Item, "{{i}}", number)
		item = strings.ReplaceAll(item, "{{fill}}", strings.Repeat("x", max(g.Width-len(number), 0)))
		out.WriteString(strings.ReplaceAll(item, "{{blob}}", blob))
	}
	out.WriteString(g.Suffix)
	return out.Bytes()
}

// milestone uses CLOCK_MONOTONIC so it is comparable with the runner and pb-meter.
type milestone struct {
	Request                      int    `json:"request"`
	ReceivedMonotonicNS          int64  `json:"received_monotonic_ns"`
	HeadersMonotonicNS           int64  `json:"headers_monotonic_ns,omitempty"`
	FirstBodyMonotonicNS         int64  `json:"first_body_monotonic_ns,omitempty"`
	FinalBodyMonotonicNS         int64  `json:"final_body_monotonic_ns,omitempty"`
	FinalWriteStartedMonotonicNS int64  `json:"final_write_started_monotonic_ns,omitempty"`
	BodyBytesWritten             int    `json:"body_bytes_written"`
	UncompressedBodyBytes        int    `json:"uncompressed_body_bytes"`
	AcceptEncoding               string `json:"accept_encoding"`
}

func monotonicNS() int64 {
	var ts unix.Timespec
	if err := unix.ClockGettime(unix.CLOCK_MONOTONIC, &ts); err != nil {
		return 0
	}
	return ts.Nano()
}

// prepare computes every response payload before the ready line, so generation
// and compression never count toward the measured CLI's latency.
func (s *scenario) prepare() error {
	for i := range s.Routes {
		r := &s.Routes[i]
		body := []byte(r.Body)
		if r.Generate != nil {
			body = r.Generate.expand()
		}
		r.payload, r.uncompressed = body, len(body)
		if r.Gzip {
			var compressed bytes.Buffer
			writer := gzip.NewWriter(&compressed)
			if _, err := writer.Write(body); err != nil {
				return err
			}
			if err := writer.Close(); err != nil {
				return err
			}
			r.payload = compressed.Bytes()
		}
	}
	return nil
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
	// Phase6 enables phase6.go; absent, the fixture behaves exactly as before.
	Phase6 *p6Config `json:"phase6"`
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
	plan       scenario
	grpcPort   int
	mu         sync.Mutex
	requests   []request
	milestones []milestone
	p6         *phase6
}

func (f *fixture) mark(index int, update func(*milestone)) {
	f.mu.Lock()
	defer f.mu.Unlock()
	update(&f.milestones[index])
}

var contractHeaders = []string{"authorization", "x-api-key", "x-p-tenant", "cookie", "content-type", "accept"}

func (f *fixture) record(r request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.p6 != nil && len(f.requests) >= f.p6.cfg.TraceLimit {
		f.p6.traceDropped.Add(1)
		return
	}
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
	if r.Method == http.MethodGet && r.URL.Path == "/__harness/milestones" {
		f.mu.Lock()
		defer f.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(f.milestones)
		return
	}
	if f.p6 != nil && f.p6.serveControl(w, r) {
		return
	}
	received := monotonicNS()
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
	entry.Unexpected = selected == nil && (f.p6 == nil || f.p6.route(r) == nil)
	f.record(entry)
	f.mu.Lock()
	index := len(f.milestones)
	f.milestones = append(f.milestones, milestone{Request: index, ReceivedMonotonicNS: received, AcceptEncoding: r.Header.Get("Accept-Encoding")})
	f.mu.Unlock()
	if f.p6 != nil {
		var done func()
		w, done = f.p6.observeHTTP(w, r, received, entry.Headers, index)
		defer done()
		if f.p6.serveRoute(f, w, r, index) {
			return
		}
	}
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
	response := selected.payload
	if response == nil {
		response = []byte(selected.Body)
	}
	if selected.Generate == nil && !selected.Gzip {
		response = []byte(strings.ReplaceAll(string(response), "{{grpc_port}}", fmt.Sprint(f.grpcPort)))
	}
	if selected.Gzip {
		w.Header().Set("Content-Encoding", "gzip")
	}
	if selected.ChunkBytes <= 0 && (selected.Gzip || selected.Generate != nil) {
		// Pre-phase-three routes keep net/http's implicit framing unchanged.
		w.Header().Set("Content-Length", strconv.Itoa(len(response)))
	}
	w.WriteHeader(code)
	f.mark(index, func(m *milestone) {
		m.HeadersMonotonicNS = monotonicNS()
		m.UncompressedBodyBytes = selected.uncompressed
	})
	defer f.mark(index, func(m *milestone) { m.FinalBodyMonotonicNS = monotonicNS() })
	chunk := selected.ChunkBytes
	if chunk <= 0 {
		chunk = len(response) + 1
	}
	for offset := 0; offset < len(response); offset += chunk {
		end := min(offset+chunk, len(response))
		// A lower bound on final body delivery, recorded before bytes can reach
		// the client. The post-write handler timestamp can race client output.
		if end == len(response) {
			f.mark(index, func(m *milestone) { m.FinalWriteStartedMonotonicNS = monotonicNS() })
		}
		if _, err := w.Write(response[offset:end]); err != nil {
			return
		}
		f.mark(index, func(m *milestone) {
			if m.FirstBodyMonotonicNS == 0 {
				m.FirstBodyMonotonicNS = monotonicNS()
			}
			m.BodyBytesWritten += end - offset
		})
		if selected.ChunkBytes > 0 {
			w.(http.Flusher).Flush()
		}
		if end < len(response) && !pause(r.Context(), selected.ChunkDelayMS) {
			return
		}
	}
}

func (f *fixture) DoGet(ticket *flight.Ticket, stream flight.FlightService_DoGetServer) error {
	if f.p6 != nil {
		if handled, err := f.p6.doGet(f, ticket, stream); handled {
			return err
		}
	}
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
	listCapabilities := flag.Bool("capabilities", false, "print supported optional route features as JSON and exit")
	flag.Parse()
	if *listCapabilities {
		return json.NewEncoder(os.Stdout).Encode(capabilities)
	}
	data, err := os.ReadFile(*path)
	if err != nil {
		return err
	}
	f := &fixture{requests: []request{}}
	if err := json.Unmarshal(data, &f.plan); err != nil {
		return err
	}
	if err := f.plan.prepare(); err != nil {
		return err
	}
	host := "127.0.0.1"
	if f.plan.Phase6 != nil {
		if f.p6, err = newPhase6(f.plan.Phase6); err != nil {
			return err
		}
		defer f.p6.close()
		host = f.p6.listenHost()
	}
	httpListener, err := net.Listen("tcp", net.JoinHostPort(host, "0"))
	if err != nil {
		return err
	}
	defer httpListener.Close()
	grpcListener, err := net.Listen("tcp", net.JoinHostPort(host, "0"))
	if err != nil {
		return err
	}
	defer grpcListener.Close()
	f.grpcPort = grpcListener.Addr().(*net.TCPAddr).Port
	banner := map[string]any{"url": "http://" + httpListener.Addr().String(), "grpc_port": f.grpcPort}
	var options []grpc.ServerOption
	if f.p6 != nil {
		if options, httpListener, grpcListener, err = f.p6.start(f, banner, httpListener, grpcListener); err != nil {
			return err
		}
	}
	server := grpc.NewServer(options...)
	flight.RegisterFlightServiceServer(server, f)
	go func() { _ = server.Serve(grpcListener) }()
	if err := json.NewEncoder(os.Stdout).Encode(banner); err != nil {
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
