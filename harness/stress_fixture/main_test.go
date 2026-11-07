package main

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"
	"unicode/utf8"
)

func mustConfig(t *testing.T, js string) Config {
	t.Helper()
	cfg, err := parseConfig([]byte(js))
	if err != nil {
		t.Fatalf("parseConfig(%s): %v", js, err)
	}
	return cfg
}

// generate returns the full logical body via the emitter with an in-memory sink.
func generate(t *testing.T, cfg Config) []byte {
	t.Helper()
	var out bytes.Buffer
	e := newEmitter(context.Background(), cfg, func(b []byte) error { out.Write(b); return nil })
	if _, err := e.run(); err != nil {
		t.Fatal(err)
	}
	return out.Bytes()
}

type row struct {
	I int64  `json:"i"`
	S string `json:"s"`
}

func decodeRowStrict(t *testing.T, b []byte) row {
	t.Helper()
	var m map[string]json.RawMessage
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatalf("row %q: %v", b, err)
	}
	if len(m) != 2 || m["i"] == nil || m["s"] == nil {
		t.Fatalf("row keys %q", b)
	}
	var r row
	if err := json.Unmarshal(b, &r); err != nil {
		t.Fatal(err)
	}
	return r
}

func TestRowBoundarySizes(t *testing.T) {
	ids := []int64{0, 1, 9, 10, 61, 92, 999, 123456789, 1<<62 + 7}
	sizes := []int{20, 21, 22, 23, 31, 32, 33, 47, 63, 64, 65, 100, 255, 4096, 65537}
	for name, p := range shapes {
		for _, i := range ids {
			for _, size := range sizes {
				b := appendRow(nil, p, i, size)
				if want := rowLen(i, size); len(b) != want {
					t.Fatalf("%s i=%d size=%d: len %d want %d", name, i, size, len(b), want)
				}
				if !utf8.Valid(b) {
					t.Fatalf("%s: invalid utf8 %q", name, b)
				}
				if r := decodeRowStrict(t, b); r.I != i {
					t.Fatalf("%s: id %d want %d", name, r.I, i)
				}
			}
		}
	}
	// Row length never shrinks below the natural minimum.
	if got := len(appendRow(nil, shapes["tiny"], 1<<62, 20)); got != rowOverhead+19 {
		t.Fatalf("large id row len %d", got)
	}
}

func TestLargeRowAtLimit(t *testing.T) {
	for _, name := range []string{"ascii", "mixed"} {
		b := appendRow(nil, shapes[name], 5, maxRowBytes)
		if len(b) != maxRowBytes || !json.Valid(b) {
			t.Fatalf("%s: len %d valid %v", name, len(b), json.Valid(b))
		}
	}
}

func TestDeterministicIDsAndShapes(t *testing.T) {
	for _, shape := range []string{"tiny", "ascii", "escaped", "mixed"} {
		cfg := mustConfig(t, fmt.Sprintf(`{"rows":500,"row_bytes":57,"shape":%q,"chunk_bytes":7}`, shape))
		a := generate(t, cfg)
		cfg.ChunkBytes = 65536
		if b := generate(t, cfg); !bytes.Equal(a, b) {
			t.Fatalf("%s: body depends on chunk size", shape)
		}
		var raw []json.RawMessage
		if err := json.Unmarshal(a, &raw); err != nil {
			t.Fatalf("%s: %v", shape, err)
		}
		if len(raw) != 500 {
			t.Fatalf("%s: %d rows", shape, len(raw))
		}
		for i, rr := range raw {
			if r := decodeRowStrict(t, rr); r.I != int64(i) {
				t.Fatalf("%s: row %d id %d", shape, i, r.I)
			}
		}
		sum, size, rows := expectedBody(cfg)
		h := sha256.Sum256(a)
		if sum != hex.EncodeToString(h[:]) || size != int64(len(a)) || rows != 500 {
			t.Fatalf("%s: expectedBody mismatch", shape)
		}
	}
}

func TestEscapedAndMixedUTF8(t *testing.T) {
	esc := decodeRowStrict(t, appendRow(nil, shapes["escaped"], 0, 200)).S
	for _, want := range []string{"\"", "\\", "/", "\b", "\f", "\n", "\r", "\t", "\x00", "\x1f", "é", "€", "😀"} {
		if !strings.Contains(esc, want) {
			t.Fatalf("escaped payload lacks %q: %q", want, esc)
		}
	}
	rawEsc := appendRow(nil, shapes["escaped"], 0, 200)
	for _, c := range rawEsc {
		if c >= 0x80 {
			t.Fatal("escaped shape must be ASCII on the wire")
		}
	}
	for _, want := range []string{"\\u0000", "\\u00e9", "\\ud83d\\ude00", "\\\""} {
		if !bytes.Contains(rawEsc, []byte(want)) {
			t.Fatalf("escaped wire form lacks %s: %s", want, rawEsc)
		}
	}
	if raw := appendRow(nil, shapes["mixed"], 0, 200); !bytes.Contains(raw, []byte("\\u00e9")) || !bytes.Contains(raw, []byte("é")) {
		t.Fatalf("mixed must carry both raw UTF-8 and \\u escapes: %s", raw)
	}
	mixed := decodeRowStrict(t, appendRow(nil, shapes["mixed"], 3, 200)).S
	for _, want := range []string{"é", "€", "😀", "日本", "\"", "\n"} {
		if !strings.Contains(mixed, want) {
			t.Fatalf("mixed payload lacks %q", want)
		}
	}
	// Every row of every size is valid UTF-8, including the cut-off at odd sizes.
	for size := 20; size < 120; size++ {
		for i := int64(0); i < 13; i++ {
			b := appendRow(nil, shapes["mixed"], i, size)
			if !utf8.Valid(b) || len(b) != size {
				t.Fatalf("mixed i=%d size=%d", i, size)
			}
		}
	}
	// Different rows rotate the payload.
	if a, b := appendRow(nil, shapes["ascii"], 1, 64), appendRow(nil, shapes["ascii"], 2, 64); bytes.Equal(a[8:], b[8:]) {
		t.Fatal("ascii payload not rotated")
	}
}

func TestZeroRows(t *testing.T) {
	if got := string(generate(t, mustConfig(t, `{"rows":0}`))); got != "[]" {
		t.Fatalf("got %q", got)
	}
}

func TestConfigDefaults(t *testing.T) {
	cfg := mustConfig(t, `{"rows":3}`)
	want := Config{Rows: 3, RowBytes: 20, Shape: "tiny", ChunkBytes: 65536, Failure: "none", FragmentPattern: []int{}}
	if fmt.Sprint(cfg) != fmt.Sprint(want) {
		t.Fatalf("got %+v", cfg)
	}
}

func TestInvalidConfig(t *testing.T) {
	cases := []string{
		``,
		`[]`,
		`{}`,
		`{"rows":-1}`,
		`{"rows":1.5}`,
		`{"rows":"1"}`,
		`{"rows":1,"row_bytes":19}`,
		`{"rows":1,"row_bytes":16777217}`,
		`{"rows":1,"shape":"huge"}`,
		`{"rows":1,"chunk_bytes":0}`,
		`{"rows":1,"chunk_bytes":16777217}`,
		`{"rows":1,"chunk_delay_ms":-1}`,
		`{"rows":1,"chunk_delay_ms":60001}`,
		`{"rows":1,"failure":"explode"}`,
		`{"rows":1,"fail_after_rows":0}`,
		`{"rows":1,"failure":"status503","fail_after_rows":0}`,
		`{"rows":1,"failure":"truncate","fail_after_rows":1}`,
		`{"rows":0,"failure":"truncate"}`,
		`{"rows":1,"failure":"invalid_json","fail_after_rows":2}`,
		`{"rows":3,"failure":"truncate","fail_after_rows":-1}`,
		`{"rows":1,"fragment_pattern":[0]}`,
		`{"rows":1,"fragment_pattern":[1048577]}`,
		`{"rows":1,"unknown":true}`,
		`{"rows":1} {"rows":2}`,
		`{"rows":1,"gzip":"yes"}`,
	}
	for _, c := range cases {
		if _, err := parseConfig([]byte(c)); err == nil {
			t.Errorf("accepted %s", c)
		}
	}
	big := `{"rows":1,"fragment_pattern":[` + strings.Repeat("1,", maxFragmentEntries) + `1]}`
	if _, err := parseConfig([]byte(big)); err == nil {
		t.Error("accepted oversized fragment_pattern")
	}
	if _, err := parseConfig(bytes.Repeat([]byte(" "), maxConfigBytes+1)); err == nil {
		t.Error("accepted oversized config")
	}
}

// Generation allocations must not scale with rows.
func TestAllocationsIndependentOfRows(t *testing.T) {
	allocs := func(rows int64) float64 {
		cfg := mustConfig(t, fmt.Sprintf(`{"rows":%d,"row_bytes":40,"shape":"mixed","chunk_bytes":4096}`, rows))
		return testing.AllocsPerRun(3, func() {
			e := newEmitter(context.Background(), cfg, func([]byte) error { return nil })
			if _, err := e.run(); err != nil {
				t.Fatal(err)
			}
		})
	}
	small, large := allocs(10), allocs(200000)
	if large > small {
		t.Fatalf("allocations scale with rows: %v vs %v", small, large)
	}
}

type fixture struct {
	t   *testing.T
	srv *httptest.Server
}

func startFixture(t *testing.T, js string) *fixture {
	t.Helper()
	srv := httptest.NewServer(newServer(mustConfig(t, js)))
	t.Cleanup(srv.Close)
	return &fixture{t: t, srv: srv}
}

func (f *fixture) query(ctx context.Context, body string, auth bool) *http.Response {
	f.t.Helper()
	req, _ := http.NewRequestWithContext(ctx, http.MethodPost, f.srv.URL+queryPath, strings.NewReader(body))
	if auth {
		req.SetBasicAuth("harness", "harness")
	}
	tr := &http.Transport{DisableCompression: true}
	f.t.Cleanup(tr.CloseIdleConnections)
	resp, err := (&http.Client{Transport: tr}).Do(req)
	if err != nil {
		f.t.Fatal(err)
	}
	return resp
}

func (f *fixture) stats(query string) statsResponse {
	f.t.Helper()
	resp, err := http.Get(f.srv.URL + statsPath + query)
	if err != nil {
		f.t.Fatal(err)
	}
	defer resp.Body.Close()
	var s statsResponse
	if err := json.NewDecoder(resp.Body).Decode(&s); err != nil {
		f.t.Fatal(err)
	}
	return s
}

func (f *fixture) waitIdle() statsResponse {
	f.t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for {
		s := f.stats("")
		if s.ActiveQueries == 0 && s.LastQuery != nil {
			return s
		}
		if time.Now().After(deadline) {
			f.t.Fatalf("handler did not finish: %+v", s)
		}
		time.Sleep(5 * time.Millisecond)
	}
}

func sha(b []byte) string {
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:])
}

func TestStreamHashIndependentOfTransportSettings(t *testing.T) {
	base := `"rows":3000,"row_bytes":45,"shape":"mixed"`
	want := ""
	for _, extra := range []string{
		`"chunk_bytes":65536`,
		`"chunk_bytes":1`,
		`"chunk_bytes":777,"flush":true`,
		`"chunk_bytes":4096,"fragment_pattern":[1,2,3,500],"flush":true`,
		`"chunk_bytes":333,"gzip":true`,
		`"chunk_bytes":4096,"gzip":true,"flush":true,"fragment_pattern":[3,1]`,
	} {
		f := startFixture(t, "{"+base+","+extra+"}")
		resp := f.query(context.Background(), `{"query":"select 1"}`, true)
		raw, err := io.ReadAll(resp.Body)
		resp.Body.Close()
		if err != nil {
			t.Fatalf("%s: %v", extra, err)
		}
		body := raw
		gz := strings.Contains(extra, "gzip")
		if got := resp.Header.Get("Content-Encoding"); (got == "gzip") != gz {
			t.Fatalf("%s: content-encoding %q", extra, got)
		}
		if gz {
			zr, err := gzip.NewReader(bytes.NewReader(raw))
			if err != nil {
				t.Fatal(err)
			}
			if body, err = io.ReadAll(zr); err != nil {
				t.Fatalf("%s: %v", extra, err)
			}
		}
		if !json.Valid(body) {
			t.Fatalf("%s: invalid JSON", extra)
		}
		s := f.waitIdle()
		if want == "" {
			want = sha(body)
		}
		lq := s.LastQuery
		if sha(body) != want || s.ResponseSHA256 != want || !s.Completed || lq.Outcome != "complete" {
			t.Fatalf("%s: hash/outcome mismatch %+v", extra, lq)
		}
		if s.RowsWritten != 3000 || s.SourceBytes != int64(len(body)) || s.WireBytes != int64(len(raw)) {
			t.Fatalf("%s: counters %+v", extra, s)
		}
		if lq.BodySHA256 != sha([]byte(`{"query":"select 1"}`)) || lq.Auth != "ok" || lq.Method != "POST" || lq.Path != queryPath {
			t.Fatalf("%s: request record %+v", extra, lq)
		}
		if lq.FirstWriteUnix == 0 || lq.LastWriteUnix < lq.FirstWriteUnix {
			t.Fatalf("%s: write times %+v", extra, lq)
		}
		if e := f.stats("?expected=1"); e.ExpectedSHA256 != want || *e.ExpectedBytes != int64(len(body)) || *e.ExpectedRows != 3000 {
			t.Fatalf("%s: expected %+v", extra, e)
		}
	}
}

func TestFragmentPatternWriteCount(t *testing.T) {
	cfg := `{"rows":40,"row_bytes":30,"chunk_bytes":1048576,"fragment_pattern":[1,2,3],"flush":true}`
	f := startFixture(t, cfg)
	resp := f.query(context.Background(), "", true)
	body, err := io.ReadAll(resp.Body)
	resp.Body.Close()
	if err != nil {
		t.Fatal(err)
	}
	n, writes := len(body), 0
	for k := 0; n > 0; k++ {
		n -= []int{1, 2, 3}[k%3]
		writes++
	}
	s := f.waitIdle()
	if s.LastQuery.WireWrites != int64(writes) || s.LastQuery.Chunks != 1 {
		t.Fatalf("writes %d want %d (%+v)", s.LastQuery.WireWrites, writes, s.LastQuery)
	}
}

func expectedTruncatedPrefix(cfg Config) []byte {
	b := []byte{'['}
	for i := int64(0); i < cfg.FailAfterRows; i++ {
		if i > 0 {
			b = append(b, ',')
		}
		b = appendRow(b, shapes[cfg.Shape], i, cfg.RowBytes)
	}
	var next []byte
	if cfg.FailAfterRows > 0 {
		next = append(next, ',')
	}
	next = appendRow(next, shapes[cfg.Shape], cfg.FailAfterRows, cfg.RowBytes)
	return append(b, next[:len(next)/2]...)
}

func TestTruncateIsTransportFailure(t *testing.T) {
	for _, gz := range []bool{false, true} {
		js := fmt.Sprintf(`{"rows":100,"row_bytes":64,"shape":"escaped","chunk_bytes":100,"failure":"truncate","fail_after_rows":17,"gzip":%v,"fragment_pattern":[7,1],"flush":true}`, gz)
		f := startFixture(t, js)
		resp := f.query(context.Background(), "q", true)
		raw, err := io.ReadAll(resp.Body)
		resp.Body.Close()
		if !errors.Is(err, io.ErrUnexpectedEOF) {
			t.Fatalf("gzip=%v: want unexpected EOF, got %v", gz, err)
		}
		body := raw
		if gz {
			zr, zerr := gzip.NewReader(bytes.NewReader(raw))
			if zerr != nil {
				t.Fatal(zerr)
			}
			body, err = io.ReadAll(zr)
			if !errors.Is(err, io.ErrUnexpectedEOF) {
				t.Fatalf("gzip stream should be cut: %v", err)
			}
		}
		want := expectedTruncatedPrefix(mustConfig(t, js))
		if !bytes.Equal(body, want) {
			t.Fatalf("gzip=%v: prefix mismatch\n got %q\nwant %q", gz, body, want)
		}
		s := f.waitIdle()
		lq := s.LastQuery
		if lq.Outcome != "truncated" || lq.Completed || lq.RowsWritten != 17 || lq.ResponseSHA256 != sha(want) || lq.SourceBytes != int64(len(want)) {
			t.Fatalf("gzip=%v: record %+v", gz, lq)
		}
		if e := f.stats("?expected=1"); e.ExpectedSHA256 != sha(want) {
			t.Fatal("expected hash must describe the truncated body")
		}
	}
}

func TestInvalidJSON(t *testing.T) {
	for _, after := range []int64{0, 5} {
		f := startFixture(t, fmt.Sprintf(`{"rows":10,"failure":"invalid_json","fail_after_rows":%d}`, after))
		resp := f.query(context.Background(), "", true)
		body, err := io.ReadAll(resp.Body)
		resp.Body.Close()
		if err != nil || resp.StatusCode != 200 {
			t.Fatalf("%v %d", err, resp.StatusCode)
		}
		if json.Valid(body) {
			t.Fatalf("body is valid JSON: %s", body)
		}
		dec := json.NewDecoder(bytes.NewReader(body))
		dec.Token() // [
		for i := int64(0); i < after; i++ {
			var r row
			if err := dec.Decode(&r); err != nil || r.I != i {
				t.Fatalf("prefix row %d: %v", i, err)
			}
		}
		if !bytes.HasSuffix(body, []byte(fmt.Sprintf(`{"i":%d,"s":NaN}]`, after))) {
			t.Fatalf("suffix %q", body)
		}
		if lq := f.waitIdle().LastQuery; lq.Outcome != "invalid_json" || lq.Completed || lq.RowsWritten != after {
			t.Fatalf("%+v", lq)
		}
	}
}

func TestStatus503(t *testing.T) {
	f := startFixture(t, `{"rows":10,"failure":"status503","gzip":true}`)
	resp := f.query(context.Background(), "", true)
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != 503 || string(body) != status503Body || resp.Header.Get("Content-Encoding") != "" {
		t.Fatalf("%d %q", resp.StatusCode, body)
	}
	if lq := f.waitIdle().LastQuery; lq.Outcome != "status503" || lq.Status != 503 {
		t.Fatalf("%+v", lq)
	}
}

func TestAuthMethodAndRequestLimit(t *testing.T) {
	f := startFixture(t, `{"rows":1}`)
	resp := f.query(context.Background(), "x", false)
	resp.Body.Close()
	if resp.StatusCode != 401 || f.waitIdle().LastQuery.Auth != "missing" {
		t.Fatal("missing auth not rejected")
	}
	req, _ := http.NewRequest(http.MethodPost, f.srv.URL+queryPath, nil)
	req.SetBasicAuth("harness", "wrong")
	resp, _ = http.DefaultClient.Do(req)
	resp.Body.Close()
	if resp.StatusCode != 401 || f.waitIdle().LastQuery.Auth != "invalid" {
		t.Fatal("bad password not rejected")
	}
	req, _ = http.NewRequest(http.MethodGet, f.srv.URL+queryPath, nil)
	req.SetBasicAuth("harness", "harness")
	resp, _ = http.DefaultClient.Do(req)
	resp.Body.Close()
	if resp.StatusCode != 405 || f.waitIdle().LastQuery.Method != "GET" {
		t.Fatal("GET not rejected")
	}

	exact := strings.Repeat("a", maxRequestBody)
	resp = f.query(context.Background(), exact, true)
	io.Copy(io.Discard, resp.Body)
	resp.Body.Close()
	lq := f.waitIdle().LastQuery
	if resp.StatusCode != 200 || lq.BodyBytes != maxRequestBody || lq.BodySHA256 != sha([]byte(exact)) || lq.BodyOverLimit {
		t.Fatalf("exact-limit body: %d %+v", resp.StatusCode, lq)
	}
	resp = f.query(context.Background(), exact+"b", true)
	resp.Body.Close()
	lq = f.waitIdle().LastQuery
	if resp.StatusCode != 413 || !lq.BodyOverLimit || lq.BodySHA256 != "" {
		t.Fatalf("over-limit body: %d %+v", resp.StatusCode, lq)
	}

	resp, _ = http.Get(f.srv.URL + "/nope")
	resp.Body.Close()
	s := f.stats("")
	if resp.StatusCode != 404 || s.Requests != 6 || s.QueryRequests != 5 || len(s.Recent) != 6 {
		t.Fatalf("counters %+v", s)
	}
}

func TestRecentIsBounded(t *testing.T) {
	f := startFixture(t, `{"rows":0}`)
	for i := 0; i < recentLimit+5; i++ {
		resp, _ := http.Get(f.srv.URL + "/missing")
		resp.Body.Close()
	}
	s := f.stats("")
	if len(s.Recent) != recentLimit || s.Recent[0].Seq != 6 || s.Recent[recentLimit-1].Seq != recentLimit+5 {
		t.Fatalf("recent %d first %d", len(s.Recent), s.Recent[0].Seq)
	}
}

func TestClientDisconnectStopsPromptly(t *testing.T) {
	for _, js := range []string{
		`{"rows":1000000000000,"row_bytes":100,"chunk_bytes":1024,"chunk_delay_ms":60000,"flush":true}`,
		`{"rows":1000000000000,"row_bytes":100,"chunk_bytes":1024,"chunk_delay_ms":50,"flush":true,"gzip":true}`,
		`{"rows":1000000000000,"row_bytes":4096,"chunk_bytes":65536}`,
	} {
		f := startFixture(t, js)
		ctx, cancel := context.WithCancel(context.Background())
		resp := f.query(ctx, "", true)
		if _, err := io.ReadFull(resp.Body, make([]byte, 10)); err != nil {
			t.Fatal(err)
		}
		start := time.Now()
		cancel()
		resp.Body.Close()
		lq := f.waitIdle().LastQuery
		if time.Since(start) > 2*time.Second || lq.Outcome != "client_disconnect" && lq.Outcome != "write_error" {
			t.Fatalf("%s: %+v", js, lq)
		}
		if lq.Completed || lq.WriteErrors != 1 {
			t.Fatalf("%+v", lq)
		}
	}
}

func TestRunReadyLineAndShutdown(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "cfg.json")
	os.WriteFile(path, []byte(`{"rows":2,"chunk_delay_ms":60000,"chunk_bytes":1,"flush":true}`), 0o600)
	pr, pw := io.Pipe()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan int, 1)
	go func() { done <- run(ctx, []string{"--config", path}, pw, io.Discard); pw.Close() }()
	br := bufio.NewReader(pr)
	line, err := br.ReadString('\n')
	if err != nil {
		t.Fatal(err)
	}
	if !regexp.MustCompile(`^\{"url":"http://127\.0\.0\.1:\d+","pid":\d+\}\n$`).MatchString(line) {
		t.Fatalf("ready line %q", line)
	}
	var ready struct {
		URL string `json:"url"`
		PID int    `json:"pid"`
	}
	json.Unmarshal([]byte(line), &ready)
	if ready.PID != os.Getpid() {
		t.Fatal("pid")
	}
	// A stream parked in a long delay must be canceled by shutdown.
	req, _ := http.NewRequest(http.MethodPost, ready.URL+queryPath, nil)
	req.SetBasicAuth("harness", "harness")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	cancel()
	select {
	case code := <-done:
		if code != 0 {
			t.Fatalf("exit %d", code)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("shutdown hung")
	}
	if rest, _ := io.ReadAll(br); len(rest) != 0 {
		t.Fatalf("extra stdout %q", rest)
	}
}

func TestRunRejectsBadConfig(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "cfg.json")
	os.WriteFile(path, []byte(`{"rows":1,"shape":"nope"}`), 0o600)
	var stdout, stderr bytes.Buffer
	if code := run(context.Background(), []string{"--config", path}, &stdout, &stderr); code != 2 || stdout.Len() != 0 {
		t.Fatalf("code %d stdout %q", code, stdout.String())
	}
	if code := run(context.Background(), nil, &stdout, &stderr); code != 2 {
		t.Fatal("missing --config accepted")
	}
	os.WriteFile(path, []byte(`{"rows":4}`), 0o600)
	stdout.Reset()
	if code := run(context.Background(), []string{"--config", path, "--print-expected"}, &stdout, &stderr); code != 0 {
		t.Fatal(code)
	}
	sum, size, _ := expectedBody(mustConfig(t, `{"rows":4}`))
	if !strings.Contains(stdout.String(), sum) || !strings.Contains(stdout.String(), fmt.Sprintf(`"expected_source_bytes":%d`, size)) {
		t.Fatalf("print-expected %q", stdout.String())
	}
}
