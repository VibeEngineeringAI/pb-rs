package main

import (
	"compress/gzip"
	"context"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/apache/arrow/go/v13/arrow/flight"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

func TestHTTPFixtureAuthAndUnexpectedRequests(t *testing.T) {
	f := &fixture{plan: scenario{Headers: map[string]string{"x-api-key": "fixture-key"}, Routes: []route{{Method: "GET", Path: "/api/v1/about", Body: `{"grpcPort":{{grpc_port}}}`}}}, grpcPort: 1234}
	req := httptest.NewRequest(http.MethodGet, "/api/v1/about", nil)
	rec := httptest.NewRecorder()
	f.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("unauthenticated code = %d", rec.Code)
	}
	req.Header.Set("X-Api-Key", "fixture-key")
	rec = httptest.NewRecorder()
	f.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK || rec.Body.String() != `{"grpcPort":1234}` {
		t.Fatalf("response = %d %s", rec.Code, rec.Body.String())
	}
	req.URL.Path = "/unexpected"
	f.ServeHTTP(httptest.NewRecorder(), req)
	if len(f.requests) != 3 || !f.requests[2].Unexpected || f.requests[0].TimeUnixNS == 0 {
		t.Fatalf("requests = %+v", f.requests)
	}
}

func TestHTTPChunkedAndCanceledDelay(t *testing.T) {
	f := &fixture{plan: scenario{Routes: []route{{Method: "POST", Path: "/api/v1/query", Body: "123456789", ChunkBytes: 2}}}}
	rec := httptest.NewRecorder()
	f.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/api/v1/query", strings.NewReader(`{"query":"test"}`)))
	if rec.Body.String() != "123456789" || !rec.Flushed || f.requests[0].Body != `{"query":"test"}` {
		t.Fatalf("chunked response/trace incorrect: %s", rec.Body.String())
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if pause(ctx, 10000) {
		t.Fatal("canceled delay was not interrupted")
	}
}

func TestFlightBatchesAndTerminalStatus(t *testing.T) {
	f := &fixture{plan: scenario{Headers: map[string]string{"x-api-key": "fixture-key"}, Flight: &flightPlan{Rows: 65, BatchRows: 32}}}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	server := grpc.NewServer()
	flight.RegisterFlightServiceServer(server, f)
	go func() { _ = server.Serve(listener) }()
	defer server.Stop()
	client, err := flight.NewClientWithMiddleware(listener.Addr().String(), nil, nil, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	ctx = metadata.NewOutgoingContext(ctx, metadata.Pairs("x-api-key", "fixture-key"))
	stream, err := client.DoGet(ctx, &flight.Ticket{Ticket: []byte(`{"stream":"logs"}`)})
	if err != nil {
		t.Fatal(err)
	}
	reader, err := flight.NewRecordReader(stream)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Release()
	rows, batches := int64(0), 0
	for {
		record, err := reader.Read()
		if err != nil {
			if err == io.EOF || status.Code(err) != codes.InvalidArgument {
				t.Fatalf("terminal status = %v", err)
			}
			break
		}
		rows += record.NumRows()
		batches++
	}
	if rows != 65 || batches != 3 {
		t.Fatalf("rows=%d batches=%d", rows, batches)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.requests) != 1 || f.requests[0].Body != `{"stream":"logs"}` || f.requests[0].Protocol != "flight" {
		t.Fatalf("trace = %+v", f.requests)
	}
}

func TestGenerateMatchesPhase3Model(t *testing.T) {
	g := &generate{Prefix: "[", Item: `{"sequence":{{i}},"pad":"{{fill}}","b":"{{blob}}"}`, Separator: ",", Suffix: "]", Count: 3, Width: 2, BlobBytes: 2}
	want := `[{"sequence":0,"pad":"x","b":"xx"},{"sequence":1,"pad":"x","b":"xx"},{"sequence":2,"pad":"x","b":"xx"}]`
	if got := string(g.expand()); got != want {
		t.Fatalf("expand = %s", got)
	}
}

func TestGzipGeneratedRouteAndMilestones(t *testing.T) {
	plan := scenario{Routes: []route{{Method: "POST", Path: "/api/v1/query", Gzip: true, Generate: &generate{Prefix: "[", Item: "{{i}}", Separator: ",", Suffix: "]", Count: 4}}}}
	if err := plan.prepare(); err != nil {
		t.Fatal(err)
	}
	f := &fixture{plan: plan}
	rec := httptest.NewRecorder()
	f.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/api/v1/query", strings.NewReader("{}")))
	if rec.Header().Get("Content-Encoding") != "gzip" || rec.Header().Get("Content-Length") != strconv.Itoa(rec.Body.Len()) {
		t.Fatalf("headers = %v", rec.Header())
	}
	reader, err := gzip.NewReader(rec.Body)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(reader)
	if string(body) != "[0,1,2,3]" {
		t.Fatalf("body = %q", body)
	}
	m := f.milestones[0]
	if m.ReceivedMonotonicNS == 0 || m.HeadersMonotonicNS < m.ReceivedMonotonicNS || m.FirstBodyMonotonicNS < m.HeadersMonotonicNS ||
		m.FinalWriteStartedMonotonicNS < m.HeadersMonotonicNS || m.FinalWriteStartedMonotonicNS > m.FinalBodyMonotonicNS ||
		m.FinalBodyMonotonicNS < m.FirstBodyMonotonicNS || m.UncompressedBodyBytes != 9 || m.BodyBytesWritten != len(f.plan.Routes[0].payload) {
		t.Fatalf("milestone = %+v", m)
	}
	rec = httptest.NewRecorder()
	f.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/__harness/milestones", nil))
	if !strings.Contains(rec.Body.String(), `"uncompressed_body_bytes":9`) {
		t.Fatalf("milestones endpoint = %s", rec.Body.String())
	}
}

type observingWriter struct {
	*httptest.ResponseRecorder
	fixture *fixture
	testing *testing.T
}

func (w observingWriter) Write(body []byte) (int, error) {
	// The final chunk timestamp must already be published when its bytes are
	// handed to net/http, including a one-chunk response.
	if string(body) == "ef" && w.fixture.milestones[0].FinalWriteStartedMonotonicNS == 0 {
		w.testing.Fatal("final write lower bound was recorded after Write")
	}
	return w.ResponseRecorder.Write(body)
}

func TestFinalWriteLowerBoundPrecedesDelivery(t *testing.T) {
	f := &fixture{plan: scenario{Routes: []route{{Method: "GET", Path: "/x", Body: "abcdef", ChunkBytes: 2}}}}
	f.ServeHTTP(observingWriter{httptest.NewRecorder(), f, t}, httptest.NewRequest(http.MethodGet, "/x", nil))
	m := f.milestones[0]
	if m.FinalWriteStartedMonotonicNS < m.FirstBodyMonotonicNS || m.FinalWriteStartedMonotonicNS > m.FinalBodyMonotonicNS {
		t.Fatalf("invalid final chunk observation: %+v", m)
	}
}

func TestLegacyRouteFramingUnchanged(t *testing.T) {
	plan := scenario{Routes: []route{{Method: "GET", Path: "/x", Body: `{"p":{{grpc_port}}}`}}}
	if err := plan.prepare(); err != nil {
		t.Fatal(err)
	}
	f := &fixture{plan: plan, grpcPort: 7}
	rec := httptest.NewRecorder()
	f.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/x", nil))
	if rec.Body.String() != `{"p":7}` || rec.Header().Get("Content-Length") != "" || rec.Header().Get("Content-Encoding") != "" {
		t.Fatalf("legacy response changed: %v %s", rec.Header(), rec.Body.String())
	}
}
