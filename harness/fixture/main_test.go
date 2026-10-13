package main

import (
	"context"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
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
