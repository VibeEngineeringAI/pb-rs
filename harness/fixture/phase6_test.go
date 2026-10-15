package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/apache/arrow/go/v13/arrow/array"
	"github.com/apache/arrow/go/v13/arrow/flight"
	"golang.org/x/net/http2"
	"golang.org/x/net/http2/hpack"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"
)

type p6Harness struct {
	f       *fixture
	client  flight.Client
	banner  map[string]any
	httpURL string
	dir     string
}

// tlsDial trusts the fixture's generated CA; it runs after the CA file exists so
// the background dial never observes a partially built pool.
func tlsDial(serverName string) func(caPEM []byte) []grpc.DialOption {
	return func(caPEM []byte) []grpc.DialOption {
		pool := x509.NewCertPool()
		pool.AppendCertsFromPEM(caPEM)
		return []grpc.DialOption{grpc.WithTransportCredentials(credentials.NewTLS(&tls.Config{ServerName: serverName, MinVersion: tls.VersionTLS12, RootCAs: pool}))}
	}
}

func startPhase6(t *testing.T, cfg *p6Config, dialFor ...func(caPEM []byte) []grpc.DialOption) *p6Harness {
	t.Helper()
	dir := t.TempDir()
	if cfg.EventsPath == "" {
		cfg.EventsPath = filepath.Join(dir, "events.jsonl")
	}
	if cfg.SummaryPath == "" {
		cfg.SummaryPath = filepath.Join(dir, "summary.json")
	}
	f := &fixture{requests: []request{}, plan: scenario{Phase6: cfg}}
	p, err := newPhase6(cfg)
	if err != nil {
		t.Fatal(err)
	}
	f.p6 = p
	httpListener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	grpcListener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	f.grpcPort = grpcListener.Addr().(*net.TCPAddr).Port
	banner := map[string]any{"url": "http://" + httpListener.Addr().String(), "grpc_port": f.grpcPort}
	options, httpListener, grpcListener, err := p.start(f, banner, httpListener, grpcListener)
	if err != nil {
		t.Fatal(err)
	}
	server := grpc.NewServer(options...)
	flight.RegisterFlightServiceServer(server, f)
	go func() { _ = server.Serve(grpcListener) }()
	go func() { _ = http.Serve(httpListener, f) }()
	dial := []grpc.DialOption{grpc.WithTransportCredentials(insecure.NewCredentials())}
	if len(dialFor) > 0 {
		dial = dialFor[0](p.caPEM)
	}
	client, err := flight.NewClientWithMiddleware(grpcListener.Addr().String(), nil, nil, dial...)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		client.Close()
		server.Stop()
		httpListener.Close()
		p.close()
	})
	return &p6Harness{f: f, client: client, banner: banner, httpURL: banner["url"].(string), dir: dir}
}

type p6Read struct {
	stdout  []byte // Go tail framing: RecordToJSON + "\n" per batch
	batches int
	initErr error
	readErr error
}

// subscribe mirrors one iteration of the Go tail loop.
func (h *p6Harness) subscribe(t *testing.T, stream string, md ...string) p6Read {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if len(md) > 0 {
		ctx = metadata.NewOutgoingContext(ctx, metadata.Pairs(md...))
	}
	ticket, _ := json.Marshal(map[string]string{"stream": stream})
	var out p6Read
	resp, err := h.client.DoGet(ctx, &flight.Ticket{Ticket: ticket})
	if err != nil {
		out.initErr = err
		return out
	}
	reader, err := flight.NewRecordReader(resp)
	if err != nil {
		out.initErr = err
		return out
	}
	defer reader.Release()
	var stdout bytes.Buffer
	for {
		record, err := reader.Read()
		if err != nil {
			out.readErr = err
			break
		}
		var buf bytes.Buffer
		_ = array.RecordToJSON(record, &buf)
		stdout.Write(buf.Bytes())
		stdout.WriteByte('\n')
		out.batches++
	}
	out.stdout = stdout.Bytes()
	return out
}

func (h *p6Harness) summary(t *testing.T) p6Summary {
	t.Helper()
	data, err := h.f.p6.summary()
	if err != nil {
		t.Fatal(err)
	}
	var s p6Summary
	if err := json.Unmarshal(data, &s); err != nil {
		t.Fatal(err)
	}
	return s
}

func (h *p6Harness) events(t *testing.T) []map[string]any {
	t.Helper()
	h.f.p6.mu.Lock()
	h.f.p6.flushLocked()
	h.f.p6.mu.Unlock()
	file, err := os.Open(h.f.p6.cfg.EventsPath)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	var out []map[string]any
	scanner := bufio.NewScanner(file)
	scanner.Buffer(nil, 1<<20)
	for scanner.Scan() {
		var e map[string]any
		if err := json.Unmarshal(scanner.Bytes(), &e); err != nil {
			t.Fatal(err)
		}
		out = append(out, e)
	}
	return out
}

func ops(o ...p6Op) []p6Subscription { return []p6Subscription{{Ops: o}} }

func wantCode(t *testing.T, err error, code codes.Code, message string) {
	t.Helper()
	if status.Code(err) != code || (message != "" && status.Convert(err).Message() != message) {
		t.Fatalf("error = %v, want %s %q", err, code, message)
	}
}

func TestPhase6FiniteBatchesSummaryAndEvents(t *testing.T) {
	h := startPhase6(t, &p6Config{ExpectedOutput: true, Streams: map[string]*p6Stream{
		"logs": {Subscriptions: ops(p6Op{Op: "batch", Rows: 3, Count: 2}, p6Op{Op: "batch", Rows: 0})}}})
	got := h.subscribe(t, "logs")
	if got.initErr != nil || got.batches != 3 {
		t.Fatalf("read = %+v", got)
	}
	wantCode(t, got.readErr, codes.InvalidArgument, "fixture stream complete")
	if !bytes.HasSuffix(got.stdout, []byte("}\n\n\n")) || bytes.Count(got.stdout, []byte("\n")) != 9 {
		t.Fatalf("framing = %q", got.stdout)
	}
	sum := sha256.Sum256(got.stdout)
	s := h.summary(t).Streams["logs"]
	if s.Batches != 3 || s.ZeroRowBatches != 1 || s.Rows != 6 || s.NextSequence != 6 || s.ExpectedRows != 6 ||
		*s.ExpectedRenderedLines != 6 || *s.ExpectedStdoutBytes != int64(len(got.stdout)) || *s.ExpectedStdoutSHA256 != hex.EncodeToString(sum[:]) ||
		s.LastTerminal.Code != "InvalidArgument" || s.LastTerminal.Reason != "complete" || s.GRPCWireBytes <= s.EncodedBytes {
		t.Fatalf("summary = %+v", s)
	}
	kinds := map[string]int{}
	for _, e := range h.events(t) {
		kinds[e["kind"].(string)]++
		if e["kind"] == "doget" && e["ticket"] != `{"stream":"logs"}` {
			t.Fatalf("doget = %v", e)
		}
	}
	if kinds["doget"] != 1 || kinds["schema_sent"] != 1 || kinds["batch_emit_start"] != 3 || kinds["batch_emit_done"] != 3 || kinds["terminal"] != 1 {
		t.Fatalf("events = %v", kinds)
	}
	// The persisted summary is published before the terminal status.
	data, err := os.ReadFile(h.f.p6.cfg.SummaryPath)
	if err != nil || !strings.Contains(string(data), `"expected_stdout_sha256":"`+hex.EncodeToString(sum[:])) {
		t.Fatalf("summary file = %s %v", data, err)
	}
}

func TestPhase6SubscriptionStageMatrixAndExhaustion(t *testing.T) {
	h := startPhase6(t, &p6Config{Streams: map[string]*p6Stream{"logs": {Subscriptions: []p6Subscription{
		{Ops: []p6Op{{Op: "status", Code: int(codes.Unavailable), Message: "before first message"}}},
		{Ops: []p6Op{{Op: "schema"}, {Op: "eof"}}},
		{Ops: []p6Op{{Op: "batch", Rows: 2}, {Op: "status", Code: int(codes.Canceled), Message: "after batch"}}, Repeat: 2},
		{Ops: []p6Op{{Op: "headers"}, {Op: "status", Code: int(codes.Unavailable), Message: "after headers"}}},
		{Ops: []p6Op{{Op: "eof"}}},
	}}}})
	wantCode(t, h.subscribe(t, "logs").initErr, codes.Unavailable, "before first message")
	if r := h.subscribe(t, "logs"); r.initErr != nil || r.readErr != io.EOF || r.batches != 0 {
		t.Fatalf("schema-only eof = %+v", r)
	}
	for i := 0; i < 2; i++ {
		r := h.subscribe(t, "logs")
		wantCode(t, r.readErr, codes.Canceled, "after batch")
		if !strings.Contains(string(r.stdout), `"sequence":`+[]string{"0", "2"}[i]) {
			t.Fatalf("sequence did not continue across subscriptions: %s", r.stdout)
		}
	}
	wantCode(t, h.subscribe(t, "logs").initErr, codes.Unavailable, "after headers")
	if r := h.subscribe(t, "logs"); r.initErr != io.EOF {
		t.Fatalf("eof before schema must fail reader construction: %+v", r)
	}
	wantCode(t, h.subscribe(t, "logs").initErr, codes.InvalidArgument, "fixture subscriptions exhausted")
	wantCode(t, h.subscribe(t, "missing").initErr, codes.NotFound, "fixture: unknown stream")
	s := h.summary(t).Streams["logs"]
	if s.DoGets != 7 || s.Subscriptions != 6 || s.Terminals["InvalidArgument"] != 1 || s.Terminals["OK"] != 2 {
		t.Fatalf("summary = %+v", s)
	}
}

func TestPhase6SchemaKindsRoundTripThroughGoReader(t *testing.T) {
	streams := map[string]*p6Stream{}
	for kind := range p6Kinds {
		streams[kind] = &p6Stream{Schema: kind, Compression: "zstd", Subscriptions: ops(p6Op{Op: "batch", Rows: 12})}
	}
	h := startPhase6(t, &p6Config{ExpectedOutput: true, Streams: streams})
	for kind := range p6Kinds {
		r := h.subscribe(t, kind)
		if r.initErr != nil || r.batches != 1 {
			t.Errorf("%s: %+v", kind, r)
			continue
		}
		wantCode(t, r.readErr, codes.InvalidArgument, "fixture stream complete")
		sum := sha256.Sum256(r.stdout)
		if s := h.summary(t).Streams[kind]; *s.ExpectedStdoutSHA256 != hex.EncodeToString(sum[:]) {
			t.Errorf("%s: expected output differs from client rendering", kind)
		}
		switch kind {
		case "nan": // RecordToJSON stops at the first NaN row (sequence 1).
			if string(r.stdout) != "{\"sequence\":0,\"value\":0}\n\n" {
				t.Errorf("nan framing = %q", r.stdout)
			}
		case "dup":
			if !strings.Contains(string(r.stdout), `"zeta":"dup-last-0"`) {
				t.Errorf("duplicate field did not select last column: %s", r.stdout)
			}
		case "empty":
			if string(r.stdout) != strings.Repeat("{}\n", 12)+"\n" {
				t.Errorf("empty schema = %q", r.stdout)
			}
		}
	}
}

func TestPhase6DictionaryModes(t *testing.T) {
	streams := map[string]*p6Stream{}
	for _, mode := range []string{"stable", "replace", "delta"} {
		streams[mode] = &p6Stream{Schema: "dictionary", Compression: "lz4", Dictionary: &p6Dictionary{Cardinality: 3, Mode: mode, ValueBytes: 4096, MaxEntries: 6},
			Subscriptions: ops(p6Op{Op: "batch", Rows: 4, Count: 3})}
	}
	h := startPhase6(t, &p6Config{Streams: streams})
	want := map[string][]float64{"stable": {1, 0, 0}, "replace": {1, 1, 1}, "delta": {1, 1, 0}}
	for mode := range streams {
		r := h.subscribe(t, mode)
		if r.initErr != nil || r.batches != 3 {
			t.Fatalf("%s: %+v", mode, r)
		}
		if mode == "replace" && !strings.Contains(string(r.stdout), `"level":"g2-level-`) {
			t.Fatalf("replacement dictionary not decoded: %.200s", r.stdout)
		}
	}
	for mode, expected := range map[string]p6DictCounts{
		"stable":  {Messages: 1, Initial: 1, MaxSize: 3},
		"replace": {Messages: 3, Initial: 1, Replacements: 2, MaxSize: 3},
		"delta":   {Messages: 2, Initial: 1, Deltas: 1, MaxSize: 6},
	} {
		if d := h.summary(t).Streams[mode].DictionaryCounts; d == nil || *d != expected {
			t.Fatalf("%s exact dictionary = %+v want %+v", mode, d, expected)
		}
	}
	got := map[string][]float64{}
	for _, e := range h.events(t) {
		if e["kind"] == "batch_emit_done" {
			got[e["stream"].(string)] = append(got[e["stream"].(string)], e["dictionary_messages"].(float64))
			if e["decoded_bytes"].(float64) < 3*4096 || e["encoded_bytes"].(float64) >= e["decoded_bytes"].(float64) {
				t.Fatalf("compressed dictionary expansion not visible: %v", e)
			}
		}
	}
	for mode, w := range want {
		if jsonString(got[mode]) != jsonString(w) {
			t.Fatalf("%s dictionary messages = %v want %v", mode, got[mode], w)
		}
	}
}

func jsonString(v any) string {
	data, _ := json.Marshal(v)
	return string(data)
}

func TestPhase6MessageSizeBoundaryAgainstGoDefaultReceiveCap(t *testing.T) {
	const limit = 4 << 20
	h := startPhase6(t, &p6Config{Streams: map[string]*p6Stream{
		"at":   {Schema: "wide", Subscriptions: ops(p6Op{Op: "batch", Rows: 1, TargetMessageBytes: limit})},
		"over": {Schema: "wide", Subscriptions: ops(p6Op{Op: "schema"}, p6Op{Op: "batch", Rows: 1, TargetMessageBytes: limit + 1})},
	}})
	if r := h.subscribe(t, "at"); r.batches != 1 {
		t.Fatalf("message at limit = %+v", r)
	}
	r := h.subscribe(t, "over")
	if r.initErr != nil || r.batches != 0 || status.Code(r.readErr) != codes.ResourceExhausted {
		t.Fatalf("message over limit = %+v", r)
	}
	s := h.summary(t).Streams
	if s["at"].MaxMessageBytes != limit {
		t.Fatalf("max message = %d", s["at"].MaxMessageBytes)
	}
}

func TestPhase6FlightAuthAndMetadataLedger(t *testing.T) {
	h := startPhase6(t, &p6Config{FlightAuth: map[string]string{"x-api-key": "k", "x-p-tenant": "t"},
		Streams: map[string]*p6Stream{"logs": {Subscriptions: []p6Subscription{{Repeat: 2, Ops: []p6Op{{Op: "schema"}, {Op: "eof"}}}}}}})
	wantCode(t, h.subscribe(t, "logs", "x-api-key", "wrong", "x-p-tenant", "t").initErr, codes.Unauthenticated, "fixture: incorrect authentication")
	if r := h.subscribe(t, "logs", "x-api-key", "k", "x-p-tenant", "t", "cookie", "session=raw value,x"); r.initErr != nil || r.readErr != io.EOF {
		t.Fatalf("authorized = %+v", r)
	}
	var doget map[string]any
	for _, e := range h.events(t) {
		if e["kind"] == "doget" {
			doget = e
		}
	}
	md := doget["metadata"].(map[string]any)
	if doget["authority"] != net.JoinHostPort("127.0.0.1", strconv.Itoa(h.f.grpcPort)) {
		t.Fatalf("plaintext authority = %v", doget["authority"])
	}
	if doget["authorized"] != true || md["cookie"].([]any)[0] != "session=raw value,x" || !strings.HasPrefix(md["user-agent"].([]any)[0].(string), "grpc-go/") {
		t.Fatalf("doget = %v", doget)
	}
	h.f.mu.Lock()
	defer h.f.mu.Unlock()
	if len(h.f.requests) != 2 || h.f.requests[1].Headers["cookie"] != "session=raw value,x" {
		t.Fatalf("legacy trace = %+v", h.f.requests)
	}
}

func TestPhase6TLSHandshakeLedger(t *testing.T) {
	dir := t.TempDir()
	caPath := filepath.Join(dir, "ca.pem")
	cfg := &p6Config{TLS: &p6TLS{GRPC: true, CAPath: caPath}, Streams: map[string]*p6Stream{"logs": {Subscriptions: ops(p6Op{Op: "schema"}, p6Op{Op: "eof"})}}}
	h := startPhase6(t, cfg, tlsDial("localhost"))
	pem, err := os.ReadFile(caPath)
	if err != nil || !bytes.Equal(pem, h.f.p6.caPEM) || h.banner["grpc_tls"] != true || h.banner["ca_path"] != caPath {
		t.Fatalf("ca = %v %v", err, h.banner)
	}
	if r := h.subscribe(t, "logs"); r.initErr != nil || r.readErr != io.EOF {
		t.Fatalf("tls read = %+v", r)
	}
	var doget, handshake map[string]any
	for _, e := range h.events(t) {
		switch e["kind"] {
		case "doget":
			doget = e
		case "tls_handshake":
			handshake = e
		}
	}
	tlsInfo := doget["tls"].(map[string]any)
	// grpc-go takes the TLS :authority from the credential ServerName, without a port.
	if tlsInfo["server_name"] != "localhost" || tlsInfo["alpn"] != "h2" || handshake["error"] != nil || handshake["version"] != "TLS 1.3" ||
		doget["authority"] != "localhost" || h.summary(t).Streams["logs"].Authorities["localhost"] != 1 {
		t.Fatalf("tls ledger = %v %v", doget, handshake)
	}
}

func TestPhase6GatesWaitAndControlAPI(t *testing.T) {
	h := startPhase6(t, &p6Config{Streams: map[string]*p6Stream{"logs": {Subscriptions: ops(
		p6Op{Op: "schema"}, p6Op{Op: "gate", Name: "first"}, p6Op{Op: "batch", Rows: 1})}}})
	done := make(chan p6Read, 1)
	go func() { done <- h.subscribe(t, "logs") }()
	control := h.banner["control_url"].(string)
	resp, err := http.Get(control + "/__harness/phase6/wait?kind=gate_wait&name=first&timeout_ms=5000")
	if err != nil || resp.StatusCode != http.StatusOK {
		t.Fatalf("wait = %v %v", resp, err)
	}
	resp.Body.Close()
	select {
	case r := <-done:
		t.Fatalf("gate did not hold: %+v", r)
	case <-time.After(50 * time.Millisecond):
	}
	resp, err = http.Post(control+"/__harness/phase6/release?gate=first", "", nil)
	if err != nil || resp.StatusCode != http.StatusOK {
		t.Fatalf("release = %v %v", resp, err)
	}
	resp.Body.Close()
	if r := <-done; r.batches != 1 {
		t.Fatalf("after release = %+v", r)
	}
	resp, err = http.Get(control + "/__harness/phase6/wait?kind=never&timeout_ms=10")
	if err != nil || resp.StatusCode != http.StatusRequestTimeout {
		t.Fatalf("timeout wait = %v %v", resp, err)
	}
	resp.Body.Close()
	resp, err = http.Get(control + "/api/v1/about")
	if err != nil || resp.StatusCode != http.StatusNotFound {
		t.Fatalf("control must not serve routes: %v %v", resp, err)
	}
	resp.Body.Close()
}

func TestPhase6ConditionalRoutesForOAuthRefresh(t *testing.T) {
	cfg := &p6Config{HTTP: []*p6Route{
		{Method: "GET", Path: "/api/v1/about", Responses: []*p6Response{
			{Require: map[string]string{"cookie": "session=new"}, Body: `{"grpcPort":{{grpc_port}}}`},
			{Status: 401, Body: "expired"}}},
		{Method: "POST", Path: "/api/v1/cli/oauth/token", Responses: []*p6Response{
			{Require: map[string]string{"authorization": "Bearer orchestrator"}, Times: 1, Body: `{"access_token":"new","refresh_token":"r2"}`}}},
	}}
	p, err := newPhase6(cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer p.close()
	f := &fixture{requests: []request{}, plan: scenario{Headers: map[string]string{"x-api-key": "legacy"}, Phase6: cfg}, grpcPort: 4321, p6: p}
	serve := func(method, path string, headers map[string]string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(method, path, nil)
		for k, v := range headers {
			req.Header.Set(k, v)
		}
		rec := httptest.NewRecorder()
		f.ServeHTTP(rec, req)
		return rec
	}
	if rec := serve("GET", "/api/v1/about", map[string]string{"Cookie": "session=old"}); rec.Code != 401 {
		t.Fatalf("expired = %d", rec.Code)
	}
	for i, want := range []int{200, 500} {
		if rec := serve("POST", "/api/v1/cli/oauth/token", map[string]string{"Authorization": "Bearer orchestrator"}); rec.Code != want {
			t.Fatalf("refresh %d = %d", i, rec.Code)
		}
	}
	if rec := serve("GET", "/api/v1/about", map[string]string{"Cookie": "session=new"}); rec.Code != 200 || rec.Body.String() != `{"grpcPort":4321}` {
		t.Fatalf("refreshed = %d %s", rec.Code, rec.Body.String())
	}
	if rec := serve("GET", "/api/v1/unknown", nil); rec.Code != http.StatusUnauthorized {
		t.Fatalf("legacy auth still applies outside phase6 routes: %d", rec.Code)
	}
	if len(f.requests) != 5 || f.requests[0].Unexpected || !f.requests[4].Unexpected || f.requests[0].Headers["cookie"] != "session=old" {
		t.Fatalf("trace = %+v", f.requests)
	}
}

func TestPhase6CorruptionAndMetadataOnlyAreClientErrorsNotFixtureCrashes(t *testing.T) {
	cases := map[string][]p6Op{
		"metadata_first":           {{Op: "metadata_only", Count: 2}, {Op: "batch", Rows: 1}},
		"metadata_after_batch":     {{Op: "batch", Rows: 1}, {Op: "metadata_only"}, {Op: "batch", Rows: 1}},
		"garbage_header":           {{Op: "corrupt", Kind: "garbage_header"}},
		"empty_message":            {{Op: "schema"}, {Op: "corrupt", Kind: "empty_message"}},
		"truncated_body":           {{Op: "corrupt", Kind: "truncated_body", Rows: 4}},
		"foreign_batch":            {{Op: "schema"}, {Op: "corrupt", Kind: "foreign_batch"}},
		"schema_change":            {{Op: "batch", Rows: 1}, {Op: "schema", Schema: "dup"}, {Op: "batch", Rows: 1}},
		"dictionary_before_schema": {{Op: "corrupt", Kind: "dictionary_before_schema"}, {Op: "batch", Rows: 1}},
		"missing_dictionary":       {{Op: "corrupt", Kind: "missing_dictionary"}},
		"dictionary_only":          {{Op: "schema"}, {Op: "corrupt", Kind: "dictionary_only"}, {Op: "batch", Rows: 1}},
		"raw_empty":                {{Op: "raw"}},
	}
	streams := map[string]*p6Stream{}
	for name, o := range cases {
		schema := "basic"
		if strings.Contains(name, "dictionary") {
			schema = "dictionary"
		}
		streams[name] = &p6Stream{Schema: schema, Subscriptions: ops(o...)}
	}
	h := startPhase6(t, &p6Config{Streams: streams})
	for name := range cases {
		r := h.subscribe(t, name)
		t.Logf("%s: batches=%d init=%v read=%v", name, r.batches, r.initErr, r.readErr)
		if r.initErr == nil && r.readErr == nil {
			t.Errorf("%s: no terminal error", name)
		}
	}
	for _, e := range h.events(t) {
		if e["kind"] == "fixture_panic" {
			t.Fatalf("fixture panic: %v", e)
		}
	}
	if r := h.subscribe(t, "metadata_first"); r.batches != 0 && r.batches != 1 {
		t.Fatalf("fixture unusable after corruption cases: %+v", r)
	}
}

func TestPhase6LiveScheduleAndBoundedEvents(t *testing.T) {
	h := startPhase6(t, &p6Config{EventLimit: 5, EventHardLimit: 8, BatchEventSample: 10, EventSample: 1000,
		Streams: map[string]*p6Stream{"logs": {Subscriptions: ops(p6Op{Op: "live", RowsPerBatch: 1, Batches: 40, IntervalMS: 1})}}})
	start := time.Now()
	r := h.subscribe(t, "logs")
	if r.batches != 40 || time.Since(start) < 39*time.Millisecond {
		t.Fatalf("live = %d batches in %v", r.batches, time.Since(start))
	}
	events := h.events(t)
	s := h.summary(t)
	if len(events) > 8 || s.EventsWritten != int64(len(events)) || s.EventsDropped == 0 || s.EventKinds["batch_emit_done"] != 40 {
		t.Fatalf("events=%d summary=%+v", len(events), s)
	}
	resp, err := http.Get(h.banner["control_url"].(string) + "/__harness/phase6/wait?kind=batch_emit_done&stream=logs&count=40&timeout_ms=100")
	if err != nil || resp.StatusCode != http.StatusOK {
		t.Fatalf("exact wait counters = %v %v", resp, err)
	}
	resp.Body.Close()
}

func TestPhase6RejectsUnknownScriptValues(t *testing.T) {
	for _, cfg := range []*p6Config{
		{Streams: map[string]*p6Stream{"x": {Schema: "nope"}}},
		{Streams: map[string]*p6Stream{"x": {Subscriptions: ops(p6Op{Op: "teleport"})}}},
		{Streams: map[string]*p6Stream{"x": {Subscriptions: ops(p6Op{Op: "corrupt", Kind: "nope"})}}},
		{Streams: map[string]*p6Stream{"x": {Subscriptions: ops(p6Op{Op: "status", Code: 17})}}},
		{Streams: map[string]*p6Stream{"x": {Subscriptions: ops(p6Op{Op: "live"})}}},
		{TLS: &p6TLS{GRPC: true}},
	} {
		if _, err := newPhase6(cfg); err == nil {
			t.Errorf("accepted %+v", cfg)
		}
	}
}

// h2Probe is a raw HTTP/2 gRPC client whose PING handling the test controls,
// standing in for a client runtime that stops servicing its connection.
type h2Probe struct {
	conn           net.Conn
	framer         *http2.Framer
	keepalivePings int
	goAway         bool
}

func dialDoGet(t *testing.T, addr, stream string) *h2Probe {
	t.Helper()
	conn, err := net.Dial("tcp", addr)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	if _, err := io.WriteString(conn, http2.ClientPreface); err != nil {
		t.Fatal(err)
	}
	framer := http2.NewFramer(conn, conn)
	var block bytes.Buffer
	enc := hpack.NewEncoder(&block)
	for _, kv := range [][2]string{{":method", "POST"}, {":scheme", "http"}, {":path", "/arrow.flight.protocol.FlightService/DoGet"},
		{":authority", addr}, {"content-type", "application/grpc"}, {"te", "trailers"}} {
		_ = enc.WriteField(hpack.HeaderField{Name: kv[0], Value: kv[1]})
	}
	ticket, _ := json.Marshal(map[string]string{"stream": stream})
	msg, err := proto.Marshal(&flight.Ticket{Ticket: ticket})
	if err != nil {
		t.Fatal(err)
	}
	data := make([]byte, 5, 5+len(msg))
	binary.BigEndian.PutUint32(data[1:], uint32(len(msg)))
	if err := framer.WriteSettings(); err != nil {
		t.Fatal(err)
	}
	if err := framer.WriteHeaders(http2.HeadersFrameParam{StreamID: 1, BlockFragment: block.Bytes(), EndHeaders: true}); err != nil {
		t.Fatal(err)
	}
	if err := framer.WriteData(1, true, append(data, msg...)); err != nil {
		t.Fatal(err)
	}
	return &h2Probe{conn: conn, framer: framer}
}

// run reads frames until the deadline, answering PINGs only when ack is set.
// It returns false if the server closed the connection first.
func (p *h2Probe) run(t *testing.T, until time.Time, ack bool) bool {
	t.Helper()
	_ = p.conn.SetReadDeadline(until)
	for {
		frame, err := p.framer.ReadFrame()
		if err != nil {
			var ne net.Error
			return errors.As(err, &ne) && ne.Timeout()
		}
		switch f := frame.(type) {
		case *http2.SettingsFrame:
			if !f.IsAck() {
				_ = p.framer.WriteSettingsAck()
			}
		case *http2.PingFrame:
			if f.IsAck() {
				continue
			}
			if f.Data == ([8]byte{}) {
				p.keepalivePings++
			}
			if ack {
				_ = p.framer.WritePing(true, f.Data)
			}
		case *http2.GoAwayFrame:
			p.goAway = true
		}
	}
}

func TestPhase6ServerKeepaliveRequiresClientPingProgress(t *testing.T) {
	// grpc-go clamps server keepalive time to at least one second.
	h := startPhase6(t, &p6Config{Keepalive: &p6Keepalive{TimeMS: 1000, TimeoutMS: 300},
		Streams: map[string]*p6Stream{"idle": {Subscriptions: []p6Subscription{{Repeat: 2, Ops: []p6Op{{Op: "gate", Name: "never"}}}}}}})
	addr := net.JoinHostPort("127.0.0.1", strconv.Itoa(h.f.grpcPort))

	responsive := dialDoGet(t, addr, "idle")
	if !responsive.run(t, time.Now().Add(2600*time.Millisecond), true) || responsive.keepalivePings < 2 {
		t.Fatalf("acknowledging client lost its idle stream: pings=%d goaway=%v", responsive.keepalivePings, responsive.goAway)
	}

	stalled := dialDoGet(t, addr, "idle")
	start := time.Now()
	if stalled.run(t, start.Add(4*time.Second), false) {
		t.Fatalf("server kept a connection whose client never acknowledged PING (pings=%d)", stalled.keepalivePings)
	}
	if elapsed := time.Since(start); stalled.keepalivePings < 1 || elapsed < time.Second {
		t.Fatalf("closed after %v with %d keepalive pings", elapsed, stalled.keepalivePings)
	}
	resp, err := http.Get(h.banner["control_url"].(string) + "/__harness/phase6/wait?kind=conn_end&timeout_ms=2000")
	if err != nil || resp.StatusCode != http.StatusOK {
		t.Fatalf("conn_end event = %v %v", resp, err)
	}
	resp.Body.Close()
	resp, err = http.Get(h.banner["control_url"].(string) + "/__harness/phase6/wait?kind=terminal&stream=idle&timeout_ms=2000")
	if err != nil || resp.StatusCode != http.StatusOK {
		t.Fatalf("stalled stream terminal = %v %v", resp, err)
	}
	resp.Body.Close()
}

func TestPhase6HTTPSDiscoveryAndNoALPNFlight(t *testing.T) {
	caPath := filepath.Join(t.TempDir(), "ca.pem")
	// The Go tail client's TLS policy: minimum 1.2, profile hostname as ServerName.
	h := startPhase6(t, &p6Config{TLS: &p6TLS{GRPC: true, HTTP: true, NoALPN: true, CAPath: caPath, MaxVersion: "1.2"},
		HTTP:    []*p6Route{{Method: "GET", Path: "/api/v1/about", Responses: []*p6Response{{Body: `{"grpcPort":{{grpc_port}}}`}}}},
		Streams: map[string]*p6Stream{"logs": {Subscriptions: ops(p6Op{Op: "schema"}, p6Op{Op: "eof"})}}},
		tlsDial("127.0.0.1"))
	pem, err := os.ReadFile(caPath)
	pool := x509.NewCertPool()
	if err != nil || !pool.AppendCertsFromPEM(pem) || h.banner["http_tls"] != true || !strings.HasPrefix(h.httpURL, "https://127.0.0.1:") {
		t.Fatalf("banner = %v %v", h.banner, err)
	}
	client := &http.Client{Transport: &http.Transport{TLSClientConfig: &tls.Config{RootCAs: pool}}}
	resp, err := client.Get(h.httpURL + "/api/v1/about")
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != 200 || string(body) != `{"grpcPort":`+strconv.Itoa(h.f.grpcPort)+`}` {
		t.Fatalf("about = %d %s", resp.StatusCode, body)
	}
	// grpc-go 1.65 clients do not require ALPN; record what the server saw.
	r := h.subscribe(t, "logs")
	var handshakes []map[string]any
	for _, e := range h.events(t) {
		if e["kind"] == "tls_handshake" {
			handshakes = append(handshakes, e)
		}
	}
	t.Logf("no-ALPN flight: init=%v read=%v handshakes=%v", r.initErr, r.readErr, handshakes)
	for _, hs := range handshakes {
		if hs["error"] == nil && (hs["alpn"] != "" || hs["version"] != "TLS 1.2") {
			t.Fatalf("handshake = %v", hs)
		}
	}
	if len(handshakes) < 2 {
		t.Fatalf("expected http and grpc handshakes: %v", handshakes)
	}
}

func TestPhase6IPv6Listen(t *testing.T) {
	probe, err := net.Listen("tcp", "[::1]:0")
	if err != nil {
		t.Skipf("no IPv6 loopback: %v", err)
	}
	probe.Close()
	cfg := &p6Config{Listen: "::1"}
	p, err := newPhase6(cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer p.close()
	l, err := net.Listen("tcp", net.JoinHostPort(p.listenHost(), "0"))
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	if url := "http://" + l.Addr().String(); !strings.HasPrefix(url, "http://[::1]:") {
		t.Fatalf("url = %s", url)
	}
}

func TestPhase6ConnectionLossOpsThenReconnect(t *testing.T) {
	maxStream := uint32(math.MaxInt32)
	cases := map[string]p6Op{
		"fin":          {Op: "conn_close"},
		"rst":          {Op: "conn_reset"},
		"goaway":       {Op: "goaway"},
		"goaway_drain": {Op: "goaway", LastStreamID: maxStream, CloseAfterMS: 100},
		// settle_ms 0 is deliberately racy (grpc-go may transparently retry an
		// unprocessed stream), so it is not part of this deterministic test.
	}
	streams := map[string]*p6Stream{}
	for name, op := range cases {
		streams[name] = &p6Stream{Subscriptions: []p6Subscription{
			{Ops: []p6Op{{Op: "batch", Rows: 1}, op}},
			{Ops: []p6Op{{Op: "batch", Rows: 1}, {Op: "status", Code: int(codes.InvalidArgument), Message: "next subscription"}}},
		}}
	}
	h := startPhase6(t, &p6Config{Streams: streams})
	for name := range cases {
		first := h.subscribe(t, name)
		t.Logf("%s: batches=%d init=%v read=%v", name, first.batches, first.initErr, first.readErr)
		if first.batches != 1 || first.readErr == nil {
			t.Errorf("%s: first subscription = %+v", name, first)
			continue
		}
		// What cmd/tail.go's isStreamEnd sees; the review predicts Unavailable.
		if code := status.Code(first.readErr); first.readErr != io.EOF && code != codes.Unavailable && code != codes.Canceled {
			t.Errorf("%s: Go would treat %v as fatal", name, first.readErr)
		}
		time.Sleep(500 * time.Millisecond) // the Go reconnect delay
		second := h.subscribe(t, name)
		if status.Code(second.readErr) != codes.InvalidArgument {
			t.Fatalf("%s: second subscription = batches=%d init=%v read=%v", name, second.batches, second.initErr, second.readErr)
		}
		wantCode(t, second.readErr, codes.InvalidArgument, "next subscription")
		if !strings.Contains(string(second.stdout), `"sequence":1`) {
			t.Errorf("%s: reconnect did not continue the sequence: %s", name, second.stdout)
		}
	}
	peers := map[string][]string{}
	actions := map[string]int{}
	for _, e := range h.events(t) {
		switch e["kind"] {
		case "doget":
			peers[e["stream"].(string)] = append(peers[e["stream"].(string)], e["peer"].(string))
		case "conn_action":
			actions[e["stream"].(string)]++
		case "fixture_panic":
			t.Fatalf("panic: %v", e)
		}
	}
	for name := range cases {
		if len(peers[name]) != 2 || peers[name][0] == peers[name][1] || actions[name] == 0 {
			t.Errorf("%s: peers=%v actions=%d (want a new connection per subscription)", name, peers[name], actions[name])
		}
		if s := h.summary(t).Streams[name]; s.Terminals["Canceled"] != 1 || s.Terminals["InvalidArgument"] != 1 {
			t.Errorf("%s: terminals = %v", name, s.Terminals)
		}
	}
}

func TestPhase6OversizedTrailers(t *testing.T) {
	h := startPhase6(t, &p6Config{Streams: map[string]*p6Stream{
		"before": {Subscriptions: []p6Subscription{{Repeat: 2, Ops: []p6Op{{Op: "status", Code: int(codes.FailedPrecondition), Message: "big:", MessageBytes: 20 << 10}}}}},
		"after":  {Subscriptions: ops(p6Op{Op: "batch", Rows: 1}, p6Op{Op: "status", Code: int(codes.FailedPrecondition), MessageBytes: 64 << 10, TrailerPadBytes: 8 << 10})},
	}})
	r := h.subscribe(t, "before")
	if st := status.Convert(r.initErr); st.Code() != codes.FailedPrecondition || len(st.Message()) != 20<<10 || !strings.HasPrefix(st.Message(), "big:x") {
		t.Fatalf("20 KiB grpc-message with Go's 16 MiB header limit = %v", r.initErr)
	}
	r = h.subscribe(t, "after")
	if st := status.Convert(r.readErr); r.batches != 1 || st.Code() != codes.FailedPrecondition || len(st.Message()) != 64<<10 {
		t.Fatalf("64 KiB trailer after a batch = %d %v", r.batches, st.Code())
	}
	// A client advertising a 16 KiB header list (hyper's default) makes the
	// grpc-go server refuse the trailer rather than send it.
	small, err := flight.NewClientWithMiddleware(net.JoinHostPort("127.0.0.1", strconv.Itoa(h.f.grpcPort)), nil, nil,
		grpc.WithTransportCredentials(insecure.NewCredentials()), grpc.WithMaxHeaderListSize(16<<10))
	if err != nil {
		t.Fatal(err)
	}
	defer small.Close()
	h2 := &p6Harness{f: h.f, client: small}
	r = h2.subscribe(t, "before")
	t.Logf("16 KiB client: init=%v", r.initErr)
	if status.Code(r.initErr) == codes.FailedPrecondition {
		t.Fatalf("server sent a trailer above the advertised limit: %v", r.initErr)
	}
}

func TestPhase6ProtocolMismatchEndpoints(t *testing.T) {
	caPath := filepath.Join(t.TempDir(), "ca.pem")
	h := startPhase6(t, &p6Config{TLS: &p6TLS{CAPath: caPath},
		Endpoints: []*p6Endpoint{{Name: "bh", Kind: "blackhole", HoldMS: 3000}, {Name: "bhtls", Kind: "blackhole", TLS: true, HoldMS: 3000},
			{Name: "html", Kind: "http_status", Status: 502, ContentType: "text/html", Body: "<html>bad gateway</html>"}},
		HTTP: []*p6Route{{Method: "GET", Path: "/api/v1/about", Responses: []*p6Response{{Body: `{"grpcPort":{{port:bh}}}`}}}}})
	ports := h.banner["endpoints"].(map[string]int)
	resp, err := http.Get(h.httpURL + "/api/v1/about")
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if string(body) != `{"grpcPort":`+strconv.Itoa(ports["bh"])+`}` {
		t.Fatalf("about = %s", body)
	}
	dial := func(name string, opts ...grpc.DialOption) p6Read {
		if len(opts) == 0 {
			opts = []grpc.DialOption{grpc.WithTransportCredentials(insecure.NewCredentials())}
		}
		client, err := flight.NewClientWithMiddleware(net.JoinHostPort("127.0.0.1", strconv.Itoa(ports[name])), nil, nil, opts...)
		if err != nil {
			t.Fatal(err)
		}
		defer client.Close()
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		resp, err := client.DoGet(ctx, &flight.Ticket{Ticket: []byte(`{"stream":"logs"}`)})
		if err == nil {
			_, err = flight.NewRecordReader(resp)
		}
		return p6Read{initErr: err}
	}
	if r := dial("bh"); status.Code(r.initErr) != codes.DeadlineExceeded {
		t.Fatalf("blackhole = %v", r.initErr)
	}
	if r := dial("bhtls", tlsDial("localhost")(h.f.p6.caPEM)...); status.Code(r.initErr) != codes.DeadlineExceeded {
		t.Fatalf("tls blackhole = %v", r.initErr)
	}
	r := dial("html")
	t.Logf("502 endpoint: %v", r.initErr)
	if !strings.Contains(status.Convert(r.initErr).Message(), "502") {
		t.Fatalf("http_status endpoint = %v", r.initErr)
	}
	kinds := map[string]int{}
	for _, e := range h.events(t) {
		kinds[e["kind"].(string)]++
		if e["kind"] == "endpoint_request" && (e["content_type"] != "application/grpc" || e["proto"] != "HTTP/2.0") {
			t.Fatalf("endpoint request = %v", e)
		}
		if e["kind"] == "tls_handshake" && e["protocol"] == "blackhole" && e["error"] != nil {
			t.Fatalf("blackhole TLS = %v", e)
		}
	}
	if kinds["endpoint_accept"] < 2 || kinds["endpoint_request"] < 1 {
		t.Fatalf("endpoint events = %v", kinds)
	}
}

func TestPhase6ExactSummaryCountersSurviveEventSampling(t *testing.T) {
	const cycles = 40
	h := startPhase6(t, &p6Config{EventLimit: 4, EventHardLimit: 6, EventSample: 1000, BatchEventSample: 1000,
		FlightAuth: map[string]string{"x-api-key": "k"},
		HTTP:       []*p6Route{{Method: "GET", Path: "/api/v1/about", Responses: []*p6Response{{Body: `{"grpcPort":{{grpc_port}}}`}}}},
		Streams: map[string]*p6Stream{"logs": {Subscriptions: []p6Subscription{
			{Repeat: cycles, Ops: []p6Op{{Op: "batch", Rows: 0}, {Op: "batch", Rows: 1}, {Op: "eof"}}}}}}})
	auth := []string{"x-api-key", "k"}
	// One DoGet before discovery completes; it is rejected and consumes no subscription.
	if r := h.subscribe(t, "logs"); status.Code(r.initErr) != codes.Unauthenticated {
		t.Fatalf("unauthenticated = %+v", r)
	}
	resp, err := http.Get(h.httpURL + "/api/v1/about")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	for i := 0; i < cycles; i++ {
		if r := h.subscribe(t, "logs", auth...); r.batches != 2 || r.readErr != io.EOF {
			t.Fatalf("cycle %d = %+v", i, r)
		}
	}
	// A ticket that decodes to the stream but is not Go's json.Marshal bytes.
	ctx := metadata.NewOutgoingContext(context.Background(), metadata.Pairs(auth...))
	stream, err := h.client.DoGet(ctx, &flight.Ticket{Ticket: []byte(`{ "stream": "logs" }`)})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := flight.NewRecordReader(stream); status.Code(err) != codes.InvalidArgument {
		t.Fatalf("exhausted = %v", err)
	}
	h.subscribe(t, "elsewhere", auth...)

	s := h.summary(t)
	logs, unknown := s.Streams["logs"], s.Streams["<unknown>"]
	v := logs.RequestValidation
	total := int64(cycles + 2)
	if s.EventsDropped == 0 || s.EventsWritten > 6 {
		t.Fatalf("test needs dropped events: %+v", s)
	}
	if logs.DoGets != total || v.AuthChecks != total || v.TicketChecks != total || v.InvalidAuth != 1 || v.TicketMismatches != 1 ||
		v.UnexpectedRequests != 0 || v.DoGetBeforeAbout != 1 || logs.Subscriptions != cycles || logs.Rows != cycles ||
		logs.Batches != 2*cycles || logs.ZeroRowBatches != cycles || logs.ReconnectGapNS.Count != total-1 || logs.Terminals["OK"] != cycles {
		t.Fatalf("logs = %+v validation = %+v", logs, v)
	}
	if s.AboutRequests != 1 || s.HTTPRequests != 1 || s.AboutCompletedNS == nil || *s.FirstAboutNS > *s.AboutCompletedNS ||
		*logs.FirstDoGetNS > *s.FirstAboutNS || *logs.FirstNonemptyBatchNS < *s.AboutCompletedNS || *logs.LastDoGetNS < *logs.FirstNonemptyBatchNS {
		t.Fatalf("milestones = %+v %+v", s, logs)
	}
	if unknown == nil || unknown.DoGets != 1 || unknown.RequestValidation.UnexpectedRequests != 1 || len(s.Streams) != 2 {
		t.Fatalf("unknown streams = %+v", s.Streams)
	}
	// Control endpoints are not HTTP requests for accounting.
	resp, err = http.Get(h.banner["control_url"].(string) + "/__harness/phase6/summary")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if h.summary(t).HTTPRequests != 1 {
		t.Fatal("control request counted")
	}
}

func TestPhase6ResetInvalidTLSHandshake(t *testing.T) {
	for _, reset := range []bool{false, true} {
		caPath := filepath.Join(t.TempDir(), "ca.pem")
		h := startPhase6(t, &p6Config{TLS: &p6TLS{GRPC: true, CAPath: caPath, ResetInvalidHandshake: reset},
			Streams: map[string]*p6Stream{"logs": {Subscriptions: ops(p6Op{Op: "eof"})}}})
		addr := net.JoinHostPort("127.0.0.1", strconv.Itoa(h.f.grpcPort))
		for i := 0; i < 20 && reset; i++ {
			// A plaintext HTTP/2 client: send the preface and wait for the server's reaction.
			conn, err := net.Dial("tcp", addr)
			if err != nil {
				t.Fatal(err)
			}
			_, _ = io.WriteString(conn, http2.ClientPreface)
			_ = conn.SetReadDeadline(time.Now().Add(5 * time.Second))
			_, err = io.ReadAll(conn)
			conn.Close()
			if err == nil || !strings.Contains(err.Error(), "connection reset by peer") {
				t.Fatalf("attempt %d: want RST, got %v", i, err)
			}
		}
		// The Go plaintext gRPC client against the TLS port.
		r := h.subscribe(t, "logs")
		t.Logf("reset=%v plaintext client: %v", reset, r.initErr)
		if code := status.Code(r.initErr); code != codes.Unavailable {
			t.Fatalf("reset=%v: %v", reset, r.initErr)
		}
		if reset && !strings.Contains(r.initErr.Error(), "connection reset by peer") {
			t.Fatalf("reset handshake gave %v", r.initErr)
		}
		events := h.events(t)
		failed := 0
		for _, e := range events {
			if e["kind"] == "tls_handshake" && e["error"] != nil {
				failed++
				if e["reset"] != reset {
					t.Fatalf("event = %v", e)
				}
			}
		}
		if failed == 0 {
			t.Fatal("no failed handshake recorded")
		}
	}
}

func TestPhase6RawH2GoOracle(t *testing.T) {
	ok := [][2]string{{":status", "200"}, {"content-type", "application/grpc"}}
	with := func(base [][2]string, extra ...[2]string) [][2]string {
		return append(append([][2]string{}, base...), extra...)
	}
	trailers := func(f ...[2]string) p6Act { return p6Act{Act: "trailers", Fields: f} }
	gstatus := func(code string) [2]string { return [2]string{"grpc-status", code} }
	tonicText := "protocol error: missing grpc-status trailer, stream was terminated without a final status (possible truncation by a proxy or load balancer)"
	type want struct {
		batches int
		init    bool // error at DoGet/reader construction
		code    codes.Code
		message string // exact when non-empty
	}
	cases := []struct {
		name string
		acts [][]p6Act
		want []want
	}{
		{"details_trailers_only", [][]p6Act{{{Act: "headers", EndStream: true, Fields: with(ok, gstatus("0"), [2]string{"grpc-status-details-bin", "!!"})}}},
			[]want{{init: true, code: codes.Internal, message: "transport: malformed grpc-status-details-bin: illegal base64 data at input byte 0"}}},
		{"details_after_batch", [][]p6Act{{{Act: "headers", Fields: ok}, {Act: "batch", Rows: 2}, trailers(gstatus("0"), [2]string{"grpc-status-details-bin", "!!"})}},
			[]want{{batches: 1, code: codes.Internal, message: "transport: malformed grpc-status-details-bin: illegal base64 data at input byte 0"}}},
		{"message_ff_ok", [][]p6Act{{{Act: "headers", Fields: ok}, {Act: "batch", Rows: 1}, trailers(gstatus("0"), [2]string{"grpc-message", "%FF"})}},
			[]want{{batches: 1, code: codes.OK}}},
		{"message_ff_unavailable", [][]p6Act{{{Act: "headers", Fields: ok}, {Act: "batch", Rows: 1}, trailers(gstatus("14"), [2]string{"grpc-message", "%FF"})}},
			[]want{{batches: 1, code: codes.Unavailable, message: "\xff"}}},
		{"message_ff_canceled", [][]p6Act{{{Act: "headers", Fields: ok}, {Act: "batch", Rows: 1}, trailers(gstatus("1"), [2]string{"grpc-message", "%FF"})}},
			[]want{{batches: 1, code: codes.Canceled, message: "\xff"}}},
		{"message_ff_invalid_argument", [][]p6Act{{{Act: "headers", Fields: ok}, {Act: "batch", Rows: 1}, trailers(gstatus("3"), [2]string{"grpc-message", "a%E9b"})}},
			[]want{{batches: 1, code: codes.InvalidArgument, message: "a\xe9b"}}},
		{"remote_unknown_tonic_text", [][]p6Act{{{Act: "headers", Fields: ok}, {Act: "batch", Rows: 1}, trailers(gstatus("2"), [2]string{"grpc-message", tonicText})}},
			[]want{{batches: 1, code: codes.Unknown, message: tonicText}}},
		{"end_stream_without_trailers", [][]p6Act{{{Act: "headers", Fields: ok}, {Act: "batch", Rows: 1}, {Act: "end_stream"}}},
			[]want{{batches: 1, code: codes.Internal, message: "server closed the stream without sending trailers"}}},
		{"trailers_without_status", [][]p6Act{{{Act: "headers", Fields: ok}, {Act: "batch", Rows: 1}, trailers([2]string{"x-other", "1"})}},
			[]want{{batches: 1, code: codes.Unknown}}},
		{"rst_no_error_before_schema", [][]p6Act{{{Act: "headers", Fields: ok}, {Act: "rst", Code: 0}}},
			[]want{{init: true, code: codes.Internal, message: "stream terminated by RST_STREAM with error code: NO_ERROR"}}},
		{"rst_no_error_after_batch", [][]p6Act{{{Act: "headers", Fields: ok}, {Act: "batch", Rows: 1}, {Act: "rst", Code: 0}}},
			[]want{{batches: 1, code: codes.Internal, message: "stream terminated by RST_STREAM with error code: NO_ERROR"}}},
		{"goaway_below_then_next", [][]p6Act{
			{{Act: "headers", Fields: ok}, {Act: "batch", Rows: 1}, {Act: "goaway", Last: json.RawMessage(`"below"`)}, {Act: "close"}},
			{{Act: "headers", Fields: ok}, {Act: "batch", Rows: 1}, trailers(gstatus("3"), [2]string{"grpc-message", "next subscription"})}},
			[]want{{batches: 1, code: codes.Unavailable, message: "the connection is draining"}, {batches: 1, code: codes.InvalidArgument, message: "next subscription"}}},
		{"goaway_ours_then_trailers", [][]p6Act{{{Act: "headers", Fields: ok}, {Act: "batch", Rows: 1}, {Act: "goaway", Last: json.RawMessage(`"ours"`)},
			{Act: "batch", Rows: 1}, trailers(gstatus("3"), [2]string{"grpc-message", "after goaway"})}},
			[]want{{batches: 2, code: codes.InvalidArgument, message: "after goaway"}}},
		{"http_200_html", [][]p6Act{{{Act: "headers", Fields: [][2]string{{":status", "200"}, {"content-type", "text/html"}}},
			{Act: "data", B64: base64.StdEncoding.EncodeToString([]byte("<html>bad</html>")), EndStream: true}}},
			[]want{{init: true, code: codes.Unknown, message: `transport: received unexpected content-type "text/html"`}}},
		{"http_404_grpc", [][]p6Act{{{Act: "headers", EndStream: true, Fields: [][2]string{{":status", "404"}, {"content-type", "application/grpc"}}}}},
			[]want{{init: true, code: codes.Unimplemented, message: "unexpected HTTP status code received from server: 404 (Not Found)"}}},
		{"http_503_empty", [][]p6Act{{{Act: "headers", EndStream: true, Fields: [][2]string{{":status", "503"}}}}},
			[]want{{init: true, code: codes.Unavailable, message: "unexpected HTTP status code received from server: 503 (Service Unavailable); malformed header: missing HTTP content-type"}}},
		{"http_599_no_text", [][]p6Act{{{Act: "headers", EndStream: true, Fields: [][2]string{{":status", "599"}, {"content-type", "text/plain"}}}}},
			[]want{{init: true, code: codes.Unknown, message: `unexpected HTTP status code received from server: 599 (); transport: received unexpected content-type "text/plain"`}}},
	}
	var endpoints []*p6Endpoint
	for _, c := range cases {
		e := &p6Endpoint{Name: c.name, Kind: "raw_h2"}
		for _, acts := range c.acts {
			e.Subscriptions = append(e.Subscriptions, p6RawSub{Acts: acts})
		}
		endpoints = append(endpoints, e)
	}
	h := startPhase6(t, &p6Config{ExpectedOutput: true, Endpoints: endpoints})
	ports := h.banner["endpoints"].(map[string]int)
	for _, c := range cases {
		client, err := flight.NewClientWithMiddleware(net.JoinHostPort("127.0.0.1", strconv.Itoa(ports[c.name])), nil, nil,
			grpc.WithTransportCredentials(insecure.NewCredentials()))
		if err != nil {
			t.Fatal(err)
		}
		raw := &p6Harness{f: h.f, client: client}
		for i, w := range c.want {
			if i > 0 {
				time.Sleep(500 * time.Millisecond)
			}
			r := raw.subscribe(t, "logs")
			err := r.readErr
			if w.init {
				err = r.initErr
			}
			st := status.Convert(err)
			if err == io.EOF {
				st = status.New(codes.OK, "")
			}
			t.Logf("%s[%d]: batches=%d init=%v read=%v", c.name, i, r.batches, r.initErr, r.readErr)
			if r.batches != w.batches || (w.init != (r.initErr != nil)) || st.Code() != w.code || (w.message != "" && st.Message() != w.message) {
				t.Errorf("%s[%d]: got batches=%d code=%v message=%q", c.name, i, r.batches, st.Code(), st.Message())
			}
		}
		client.Close()
	}
	s := h.summary(t)
	next := s.Streams["endpoint:goaway_below_then_next"]
	if s.Streams["endpoint:message_ff_ok"].LastTerminal.Code != "OK" || s.Streams["endpoint:trailers_without_status"].LastTerminal.Code != "raw:trailers-without-grpc-status" {
		t.Fatalf("raw terminal codes = %+v", s.Streams)
	}
	if next == nil || next.DoGets != 2 || next.Subscriptions != 2 || next.NextSequence != 2 || next.RequestValidation.TicketMismatches != 0 || next.Connections < 2 {
		t.Fatalf("raw endpoint stats = %+v", next)
	}
	var stream map[string]any
	for _, e := range h.events(t) {
		if e["kind"] == "raw_h2_stream" && e["name"] == "goaway_below_then_next" {
			stream = e
		}
		if e["kind"] == "fixture_panic" {
			t.Fatalf("panic %v", e)
		}
	}
	md := stream["metadata"].(map[string]any)
	if stream["ticket"] != `{"stream":"logs"}` || md[":path"].([]any)[0] != "/arrow.flight.protocol.FlightService/DoGet" ||
		!strings.HasPrefix(md["user-agent"].([]any)[0].(string), "grpc-go/") {
		t.Fatalf("raw stream event = %v", stream)
	}
}

// A Send mock exposes causal gates without relying on transport speed or
// assuming that a client can keep up with a duration-limited schedule.
type p6SendMock struct {
	flight.FlightService_DoGetServer
	send func(*flight.FlightData) error
}

func (m *p6SendMock) Send(fd *flight.FlightData) error { return m.send(fd) }

func mockPhase6Run(t *testing.T, spec *p6Stream, ctx context.Context, send func(*flight.FlightData) error) *p6Run {
	t.Helper()
	p, err := newPhase6(&p6Config{ExpectedOutput: true, Streams: map[string]*p6Stream{"mock": spec}})
	if err != nil {
		t.Fatal(err)
	}
	origin := monotonicNS()
	p.streams["mock"].FirstDoGetNS = &origin
	r := &p6Run{p: p, spec: spec, name: "mock", ctx: ctx, stream: &p6SendMock{send: send}}
	t.Cleanup(func() {
		if r.writer != nil {
			_ = r.writer.Close()
		}
		if r.dict != nil {
			r.dict.release()
		}
		p.close()
	})
	return r
}

func TestPhase6LiveScheduleAccounting(t *testing.T) {
	r := mockPhase6Run(t, &p6Stream{}, context.Background(), func(*flight.FlightData) error { return nil })
	start := monotonicNS()
	for _, n := range []int{10, 5} {
		if err := r.live(&p6Op{Batches: n, RowsPerBatch: 1}); err != nil {
			t.Fatal(err)
		}
	}
	s := r.p.streams[r.name]
	l := s.Live
	if l.Ops != 2 || l.StoppedByCount != 2 || *l.ScheduledBatches != 15 || l.EmittedBatches != 15 ||
		l.OmittedByDeadline != 0 || l.LatenessNS.Count != 15 || l.SendCallNS.Count != 15 || s.BatchSendCallNS != l.SendCallNS {
		t.Fatalf("live = %+v send=%+v", l, s.BatchSendCallNS)
	}
	if last := l.Last; last.StopReason != "count" || *last.Scheduled != 5 || last.EndedNS < last.StartedNS || last.StartedNS < start || last.DeadlineNS != nil {
		t.Fatalf("last = %+v", last)
	}
	cases := []struct {
		op    p6Op
		count int64
	}{
		{p6Op{DurationMS: 1800000, IntervalMS: 20}, 90000},
		{p6Op{DurationMS: 25, IntervalMS: 10}, 3},
		{p6Op{DurationMS: 25, IntervalMS: 10, Batches: 2}, 2},
		{p6Op{DurationMS: 25, IntervalMS: 10, Batches: 10}, 3},
		{p6Op{Batches: 10}, 10},
	}
	for _, c := range cases {
		if n := liveSchedule(&c.op); n == nil || *n != c.count {
			t.Fatalf("schedule %+v = %v", c.op, n)
		}
	}
	for _, n := range []int{0, 3} {
		if liveSchedule(&p6Op{DurationMS: 10, Batches: n}) != nil {
			t.Fatal("duration burst must be unbounded")
		}
	}
}

func TestPhase6LiveDeadlineCutByCausalSendGate(t *testing.T) {
	entered, release := make(chan struct{}), make(chan struct{})
	calls := 0
	r := mockPhase6Run(t, &p6Stream{}, context.Background(), func(*flight.FlightData) error {
		calls++
		if calls == 2 {
			close(entered)
			<-release
		} // schema first, then first batch
		return nil
	})
	done := make(chan error, 1)
	go func() { done <- r.live(&p6Op{RowsPerBatch: 1, IntervalMS: 1, DurationMS: 50}) }()
	<-entered
	var snapshot p6Summary
	data, err := r.p.summary()
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, &snapshot); err != nil {
		t.Fatal(err)
	}
	active := snapshot.Streams[r.name].Live
	if active.Ops != 1 || *active.ScheduledBatches != 50 || active.EmittedBatches != 0 {
		t.Fatalf("active = %+v", active)
	}
	// Holding Send until after the duration guarantees a deadline cut. No
	// inference about socket backpressure or producer throughput is made.
	<-time.NewTimer(50 * time.Millisecond).C
	close(release)
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	l := r.p.streams[r.name].Live
	if l.StoppedByDeadline != 1 || l.EmittedBatches != 1 || l.OmittedByDeadline != 49 || l.SendCallNS.Count != 1 ||
		l.LatenessNS.Count != 1 || *l.Last.DeadlineNS != l.Last.StartedNS+int64(50*time.Millisecond) || l.Last.EndedNS < *l.Last.DeadlineNS {
		t.Fatalf("deadline = %+v last=%+v", l, l.Last)
	}
}

func TestPhase6LiveCancellationAndFailedSend(t *testing.T) {
	for _, duringSend := range []bool{false, true} {
		t.Run(strconv.FormatBool(duringSend), func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			entered := make(chan struct{})
			calls := 0
			r := mockPhase6Run(t, &p6Stream{}, ctx, func(*flight.FlightData) error {
				calls++
				if calls == 2 {
					close(entered)
					if duringSend {
						<-ctx.Done()
						return ctx.Err()
					}
				}
				return nil
			})
			done := make(chan error, 1)
			go func() { done <- r.live(&p6Op{RowsPerBatch: 1, Batches: 3, IntervalMS: 3600000}) }()
			<-entered
			cancel()
			if err := <-done; !errors.Is(err, context.Canceled) {
				t.Fatalf("error = %v", err)
			}
			l := r.p.streams[r.name].Live
			want := int64(1)
			if duringSend {
				want = 0
			}
			if l.StoppedByTermination != 1 || l.EmittedBatches != want || l.OmittedByTermination != 3-want ||
				l.SendCallNS.Count != 1 || l.LatenessNS.Count != want || l.Last.StopReason != "termination" {
				t.Fatalf("cancel = %+v", l)
			}
		})
	}
}

func TestPhase6LiveLatenessIncludesSchemaPreparation(t *testing.T) {
	entered, release := make(chan struct{}), make(chan struct{})
	calls := 0
	r := mockPhase6Run(t, &p6Stream{}, context.Background(), func(*flight.FlightData) error {
		calls++
		if calls == 1 {
			close(entered)
			<-release
		}
		return nil
	})
	done := make(chan error, 1)
	go func() { done <- r.live(&p6Op{Batches: 1, RowsPerBatch: 1}) }()
	<-entered
	releasedNS := monotonicNS()
	close(release)
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	l := r.p.streams[r.name].Live
	if l.LatenessNS.Count != 1 || l.LatenessNS.Sum < releasedNS-l.Last.StartedNS {
		t.Fatalf("lateness excludes preparation: %+v last=%+v", l, l.Last)
	}
}

func TestPhase6LiveUnboundedSchedulePersistsAcrossOps(t *testing.T) {
	r := mockPhase6Run(t, &p6Stream{}, context.Background(), func(*flight.FlightData) error { return nil })
	for _, op := range []p6Op{{Batches: 2}, {Batches: 2, DurationMS: 10000}, {Batches: 2}} {
		if err := r.live(&op); err != nil {
			t.Fatal(err)
		}
	}
	l := r.p.streams[r.name].Live
	if l.ScheduledBatches != nil || l.UnboundedOps != 1 || l.Ops != 3 || l.EmittedBatches != 6 || l.StoppedByCount != 3 {
		t.Fatalf("burst = %+v", l)
	}
}

func TestPhase6CycleDictionaryChurnSurvivesEventDrops(t *testing.T) {
	h := startPhase6(t, &p6Config{EventLimit: 3, EventHardLimit: 5, EventSample: 1000, BatchEventSample: 1000, ExpectedOutput: true,
		Streams: map[string]*p6Stream{"dict": {Schema: "dictionary", Compression: "zstd", SummaryBucketMS: 60000,
			Dictionary:    &p6Dictionary{Mode: "cycle", Cardinality: 4, MaxEntries: 12, ValueBytes: 64},
			Subscriptions: []p6Subscription{{Repeat: 2, Ops: []p6Op{{Op: "live", RowsPerBatch: 6, Batches: 10, IntervalMS: 1}, {Op: "eof"}}}}}}})
	var out bytes.Buffer
	for i := 0; i < 2; i++ {
		r := h.subscribe(t, "dict")
		if r.batches != 10 || r.readErr != io.EOF {
			t.Fatalf("subscription %d = %+v", i, r)
		}
		out.Write(r.stdout)
	}
	// Sizes per subscription: 4 8 12 | 4 8 12 | 4 8 12 | 4 -> generations g0..g3.
	if !strings.Contains(out.String(), `"level":"g3-level-`) || strings.Contains(out.String(), `"level":"g4-`) {
		t.Fatalf("cycle generations not decoded as expected: %.300s", out.String())
	}
	var oracle bytes.Buffer
	for sub := 0; sub < 2; sub++ {
		for batch := 0; batch < 10; batch++ {
			for row := 0; row < 6; row++ {
				seq := sub*60 + batch*6 + row
				level := "null"
				if seq%7 != 6 {
					value := fmt.Sprintf("g%d-level-%d", batch/3, seq%(4*(batch%3+1)))
					value += strings.Repeat("a", 64-len(value))
					level = strconv.Quote(value)
				}
				fmt.Fprintf(&oracle, "{\"level\":%s,\"sequence\":%d}\n", level, seq)
			}
			oracle.WriteByte('\n')
		}
	}
	if !bytes.Equal(out.Bytes(), oracle.Bytes()) {
		t.Fatalf("cycle output differs from independent byte oracle: got %.160q want %.160q", out.Bytes(), oracle.Bytes())
	}
	s := h.summary(t)
	st := s.Streams["dict"]
	d := st.DictionaryCounts
	if s.EventsDropped == 0 || d == nil || d.Messages != 20 || d.Initial != 2 || d.Deltas != 12 || d.Replacements != 6 || d.Resets != 6 || d.MaxSize != 12 {
		t.Fatalf("dictionary counts = %+v dropped=%d", d, s.EventsDropped)
	}
	sum := sha256.Sum256(out.Bytes())
	if *st.ExpectedStdoutSHA256 != hex.EncodeToString(sum[:]) || st.Live.EmittedBatches != 20 || *st.Live.ScheduledBatches != 20 {
		t.Fatalf("stream = %+v", st)
	}
	b := st.Buckets
	if b == nil || b.BucketMS != 60000 || b.OriginNS != *st.FirstDoGetNS || len(b.Counts) != 1 || b.Overflow != 0 ||
		b.Counts[0] != (p6Bucket{Batches: 20, Rows: 120, DictionaryMessages: 20, DictionaryDeltas: 12, DictionaryReplacements: 6}) {
		t.Fatalf("buckets = %+v", b)
	}
}

func TestPhase6SummaryBucketsBoundariesAndOverflow(t *testing.T) {
	r := mockPhase6Run(t, &p6Stream{SummaryBucketMS: 1}, context.Background(), func(*flight.FlightData) error { return nil })
	origin := *r.p.streams[r.name].FirstDoGetNS
	r.p.mu.Lock()
	for _, i := range []int64{0, 2, 511} {
		c := r.bucket(origin + i*int64(time.Millisecond))
		c.Batches++
		c.Rows += i + 1
	}
	if r.bucket(origin+512*int64(time.Millisecond)) != nil || r.bucket(origin-1) != nil {
		t.Error("out of range bucket retained")
	}
	r.p.mu.Unlock()
	b := r.p.streams[r.name].Buckets
	if len(b.Counts) != 512 || b.Counts[0].Batches != 1 || b.Counts[1].Batches != 0 || b.Counts[2].Rows != 3 || b.Counts[511].Rows != 512 {
		t.Fatalf("buckets = %+v", b)
	}
	// All subsequent batch starts lie beyond the 512 slots.
	past := monotonicNS() - int64(time.Second)
	b.OriginNS = past
	for i := 0; i < 2; i++ {
		if err := r.batch(1, 0, 0, nil); err != nil {
			t.Fatal(err)
		}
	}
	if b.Overflow != 2 || len(b.Counts) != 512 {
		t.Fatalf("overflow = %+v", b)
	}
}

func TestPhase6DictionarySendCountSurvivesRecordFailure(t *testing.T) {
	calls := 0
	r := mockPhase6Run(t, &p6Stream{Schema: "dictionary", Dictionary: &p6Dictionary{Mode: "cycle", Cardinality: 2, MaxEntries: 4}}, context.Background(), func(*flight.FlightData) error {
		calls++
		if calls == 3 {
			return errors.New("record send failed")
		}
		return nil
	})
	if err := r.live(&p6Op{Batches: 3, RowsPerBatch: 1}); err == nil {
		t.Fatal("missing send error")
	}
	s := r.p.streams[r.name]
	if d := s.DictionaryCounts; d.Messages != 1 || d.Initial != 1 || d.MaxSize != 2 {
		t.Fatalf("dict = %+v", d)
	}
	if s.Batches != 0 || s.Live.OmittedByTermination != 3 || s.BatchSendCallNS.Count != 1 || s.Buckets.Counts[0].DictionaryMessages != 1 {
		t.Fatalf("stream = %+v", s)
	}
}

func TestPhase6CycleRejectsInvalidBounds(t *testing.T) {
	for _, d := range []p6Dictionary{{Cardinality: 8, MaxEntries: 4}, {Cardinality: -1}, {MaxEntries: -1}, {ValueBytes: -1}} {
		d.Mode = "cycle"
		if p, err := newPhase6(&p6Config{Streams: map[string]*p6Stream{"x": {Schema: "dictionary", Dictionary: &d}}}); err == nil {
			p.close()
			t.Fatalf("accepted invalid cycle dictionary %+v", d)
		}
	}
}

func TestPhase6StartupSummaryHasCPUAndStart(t *testing.T) {
	h := startPhase6(t, &p6Config{Streams: map[string]*p6Stream{"x": {Subscriptions: ops(p6Op{Op: "eof"})}}})
	data, err := os.ReadFile(h.f.p6.cfg.SummaryPath)
	if err != nil {
		t.Fatal(err)
	}
	var s p6Summary
	if err := json.Unmarshal(data, &s); err != nil || s.FixtureStartNS == 0 || s.MonotonicNS < s.FixtureStartNS || s.FixtureCPUUserNS+s.FixtureCPUSysNS == 0 {
		t.Fatalf("startup summary = %s %v", data, err)
	}
	for _, key := range []string{"fixture_cpu_user_ns", "fixture_cpu_system_ns", "fixture_start_ns", "expected_output_ns_total", "t_ns"} {
		var fields map[string]json.RawMessage
		if err := json.Unmarshal(data, &fields); err != nil {
			t.Fatal(err)
		}
		if _, ok := fields[key]; !ok {
			t.Fatalf("startup lacks %s", key)
		}
	}
	h.subscribe(t, "x")
	terminal := h.summary(t)
	if terminal.FixtureStartNS != s.FixtureStartNS || terminal.MonotonicNS < terminal.Streams["x"].LastTerminal.TNS || terminal.FixtureCPUUserNS < s.FixtureCPUUserNS || terminal.FixtureCPUSysNS < s.FixtureCPUSysNS {
		t.Fatalf("terminal snapshot = %+v startup=%+v", terminal, s)
	}
	// Expected output preparation is separately totaled at the root.
	r := mockPhase6Run(t, &p6Stream{}, context.Background(), func(*flight.FlightData) error { return nil })
	if err := r.batch(1, 0, 0, nil); err != nil {
		t.Fatal(err)
	}
	data, err = r.p.summary()
	if err != nil {
		t.Fatal(err)
	}
	var rendered p6Summary
	if err := json.Unmarshal(data, &rendered); err != nil {
		t.Fatal(err)
	}
	if rendered.ExpectedOutputNS != rendered.Streams[r.name].ExpectedOutputNS || rendered.ExpectedOutputNS <= 0 {
		t.Fatalf("expected output total = %+v", rendered)
	}

}
