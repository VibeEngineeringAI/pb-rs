// pb-stress-fixture is a loopback-only, disposable HTTP server that streams a
// deterministic JSON array from POST /api/v1/query for CLI stress tests. It is
// not a Parseable implementation. It uses only the Go standard library.
//
// Usage:
//
//	pb-stress-fixture --config PATH          serve; print one ready line
//	pb-stress-fixture --config PATH --print-expected
//
// When serving, exactly one line is written to stdout once the listener is
// bound: {"url":"http://127.0.0.1:PORT","pid":PID}. Nothing is logged per row
// or per request. SIGINT/SIGTERM cancel active streams and shut down.
//
// The body is the compact array "[" row ("," row)* "]" where row i (0-based) is
// {"i":i,"s":"<payload>"} for every shape. Its length is
// max(row_bytes, 13+digits(i)); payloads are rotated, whole cycles of
// shape-specific units (never split mid escape or mid UTF-8 sequence), padded
// with 'x' to the exact length. The logical body depends only on the
// generator fields (rows, row_bytes, shape, failure, fail_after_rows), never on
// chunking, fragmentation, flushing, delay or gzip.
//
// Transport: the logical body is buffered into chunk_bytes chunks; each full
// chunk goes to gzip (BestSpeed) or straight to the wire writer, followed by
// chunk_delay_ms (cancellable). fragment_pattern splits post-gzip bytes into
// cyclic ResponseWriter.Write sizes; without flush:true net/http's 4 KiB
// buffer may coalesce them (and delays are invisible to the client). flush
// flushes gzip per chunk and HTTP per write. The body always ends with an
// explicit flush so last_body_write times are when bytes left the handler.
//
// Stats semantics: source_bytes/response_sha256 cover logical (uncompressed)
// bytes accepted by the encoder; wire_bytes are post-gzip bytes accepted by
// ResponseWriter (no headers or chunk framing); rows_written counts rows whose
// final byte was accepted. GET /__stats?expected=1 also computes (once) the
// expected hash of the configured body; --print-expected prints it offline.
//
// Failure modes:
//   - truncate: fail_after_rows complete rows, then the first half of the next
//     row is flushed and the handler panics with http.ErrAbortHandler. net/http
//     then closes the connection without the terminating chunk (or the gzip
//     trailer), so clients see a genuine transport truncation.
//   - invalid_json: fail_after_rows valid rows, then {"i":N,"s":NaN} and "]";
//     the HTTP response itself completes normally.
//   - status503: plain-text 503 with a fixed body; no rows, never gzipped.
package main

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"hash"
	"io"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

const (
	maxRowBytes        = 16 << 20
	maxChunkBytes      = 16 << 20
	maxRequestBody     = 1 << 20
	maxConfigBytes     = 1 << 20
	maxFragmentEntries = 1024
	maxFragmentSize    = 1 << 20
	maxDelayMs         = 60_000
	minRowBytes        = 20
	defaultRowBytes    = 20
	defaultChunkBytes  = 65536
	recentLimit        = 32
	rowOverhead        = len(`{"i":`) + len(`,"s":"`) + len(`"}`)
	maxRowLen          = maxRowBytes + rowOverhead + 20

	authUser = "harness"
	authPass = "harness"

	queryPath = "/api/v1/query"
	statsPath = "/__stats"

	status503Body = "stress fixture: injected status 503\n"
)

// Config is the normalized fixture configuration, echoed in /__stats.
type Config struct {
	Rows            int64  `json:"rows"`
	RowBytes        int    `json:"row_bytes"`
	Shape           string `json:"shape"`
	ChunkBytes      int    `json:"chunk_bytes"`
	ChunkDelayMs    int    `json:"chunk_delay_ms"`
	Gzip            bool   `json:"gzip"`
	Failure         string `json:"failure"`
	FailAfterRows   int64  `json:"fail_after_rows"`
	FragmentPattern []int  `json:"fragment_pattern"`
	Flush           bool   `json:"flush"`
}

type rawConfig struct {
	Rows            *int64  `json:"rows"`
	RowBytes        *int    `json:"row_bytes"`
	Shape           *string `json:"shape"`
	ChunkBytes      *int    `json:"chunk_bytes"`
	ChunkDelayMs    *int    `json:"chunk_delay_ms"`
	Gzip            *bool   `json:"gzip"`
	Failure         *string `json:"failure"`
	FailAfterRows   *int64  `json:"fail_after_rows"`
	FragmentPattern []int   `json:"fragment_pattern"`
	Flush           *bool   `json:"flush"`
}

// parseConfig strictly decodes one JSON object and applies defaults.
func parseConfig(data []byte) (Config, error) {
	if len(data) > maxConfigBytes {
		return Config{}, fmt.Errorf("config exceeds %d bytes", maxConfigBytes)
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	var raw rawConfig
	if err := dec.Decode(&raw); err != nil {
		return Config{}, fmt.Errorf("decode config: %w", err)
	}
	if _, err := dec.Token(); err != io.EOF {
		return Config{}, errors.New("decode config: trailing data after object")
	}
	if raw.Rows == nil {
		return Config{}, errors.New("rows is required")
	}
	cfg := Config{
		Rows:            *raw.Rows,
		RowBytes:        defaultRowBytes,
		Shape:           "tiny",
		ChunkBytes:      defaultChunkBytes,
		Failure:         "none",
		FragmentPattern: []int{},
	}
	if raw.RowBytes != nil {
		cfg.RowBytes = *raw.RowBytes
	}
	if raw.Shape != nil {
		cfg.Shape = *raw.Shape
	}
	if raw.ChunkBytes != nil {
		cfg.ChunkBytes = *raw.ChunkBytes
	}
	if raw.ChunkDelayMs != nil {
		cfg.ChunkDelayMs = *raw.ChunkDelayMs
	}
	if raw.Gzip != nil {
		cfg.Gzip = *raw.Gzip
	}
	if raw.Failure != nil {
		cfg.Failure = *raw.Failure
	}
	if raw.FailAfterRows != nil {
		cfg.FailAfterRows = *raw.FailAfterRows
	}
	if raw.FragmentPattern != nil {
		cfg.FragmentPattern = raw.FragmentPattern
	}
	if raw.Flush != nil {
		cfg.Flush = *raw.Flush
	}
	if err := cfg.validate(raw.FailAfterRows != nil); err != nil {
		return Config{}, err
	}
	return cfg, nil
}

func (c Config) validate(failAfterSet bool) error {
	switch {
	case c.Rows < 0:
		return errors.New("rows must be >= 0")
	case c.RowBytes < minRowBytes || c.RowBytes > maxRowBytes:
		return fmt.Errorf("row_bytes must be in [%d, %d]", minRowBytes, maxRowBytes)
	case shapes[c.Shape] == nil:
		return fmt.Errorf("shape must be one of tiny, ascii, escaped, mixed")
	case c.ChunkBytes < 1 || c.ChunkBytes > maxChunkBytes:
		return fmt.Errorf("chunk_bytes must be in [1, %d]", maxChunkBytes)
	case c.ChunkDelayMs < 0 || c.ChunkDelayMs > maxDelayMs:
		return fmt.Errorf("chunk_delay_ms must be in [0, %d]", maxDelayMs)
	case len(c.FragmentPattern) > maxFragmentEntries:
		return fmt.Errorf("fragment_pattern may have at most %d entries", maxFragmentEntries)
	case c.FailAfterRows < 0:
		return errors.New("fail_after_rows must be >= 0")
	}
	for _, n := range c.FragmentPattern {
		if n < 1 || n > maxFragmentSize {
			return fmt.Errorf("fragment_pattern entries must be in [1, %d]", maxFragmentSize)
		}
	}
	switch c.Failure {
	case "none", "status503":
		if failAfterSet {
			return fmt.Errorf("fail_after_rows is not used with failure %q", c.Failure)
		}
	case "truncate":
		if c.FailAfterRows >= c.Rows {
			return errors.New("truncate requires fail_after_rows < rows")
		}
	case "invalid_json":
		if c.FailAfterRows > c.Rows {
			return errors.New("invalid_json requires fail_after_rows <= rows")
		}
	default:
		return errors.New("failure must be one of none, truncate, invalid_json, status503")
	}
	return nil
}

// pattern holds a shape's units concatenated twice so any rotation of a full
// cycle is a contiguous slice ending on a unit boundary.
type pattern struct {
	cycle []byte
	offs  []int // unit start offsets in the first copy; offs[n] == cycleLen
	clen  int
}

func newPattern(units []string) *pattern {
	p := &pattern{}
	for _, u := range units {
		p.offs = append(p.offs, len(p.cycle))
		p.cycle = append(p.cycle, u...)
	}
	p.clen = len(p.cycle)
	p.offs = append(p.offs, p.clen)
	p.cycle = append(p.cycle, p.cycle...)
	return p
}

func asciiUnits() []string {
	var units []string
	for c := byte(0x20); c <= 0x7e; c++ {
		if c != '"' && c != '\\' {
			units = append(units, string(c))
		}
	}
	return units
}

var shapes = map[string]*pattern{
	"tiny":  newPattern([]string{"x"}),
	"ascii": newPattern(asciiUnits()),
	"escaped": newPattern([]string{
		"\\\"", "\\\\", "\\/", "\\b", "\\f", "\\n", "\\r", "\\t",
		"\\u0000", "\\u001f", "\\u00e9", "\\u20ac", "\\ud83d\\ude00", "a", " ",
	}),
	"mixed": newPattern([]string{
		"m", "i", "x", " ", "é", "€", "😀", "日本", `\"`, `\n`, "\\u00e9", "-",
	}),
}

// appendPayload appends exactly n payload bytes for row i.
func appendPayload(dst []byte, p *pattern, i int64, n int) []byte {
	units := len(p.offs) - 1
	s := int(i % int64(units))
	if n >= p.clen {
		start := len(dst)
		dst = append(dst, p.cycle[p.offs[s]:p.offs[s]+p.clen]...)
		n -= p.clen
		for n >= p.clen {
			m := len(dst) - start
			if whole := (n / p.clen) * p.clen; m > whole {
				m = whole
			}
			dst = append(dst, dst[start:start+m]...)
			n -= m
		}
	}
	for k := s; ; k = (k + 1) % units {
		ul := p.offs[k+1] - p.offs[k]
		if ul > n {
			break
		}
		dst = append(dst, p.cycle[p.offs[k]:p.offs[k+1]]...)
		n -= ul
	}
	for ; n > 0; n-- {
		dst = append(dst, 'x')
	}
	return dst
}

// rowLen is the generated length of row i.
func rowLen(i int64, rowBytes int) int {
	min := rowOverhead + len(strconv.AppendInt(nil, i, 10))
	if rowBytes > min {
		return rowBytes
	}
	return min
}

// appendRow appends row i of the given shape.
func appendRow(dst []byte, p *pattern, i int64, rowBytes int) []byte {
	start := len(dst)
	dst = append(dst, `{"i":`...)
	dst = strconv.AppendInt(dst, i, 10)
	dst = append(dst, `,"s":"`...)
	pad := rowBytes - (len(dst) - start) - 2
	if pad < 0 {
		pad = 0
	}
	dst = appendPayload(dst, p, i, pad)
	return append(dst, `"}`...)
}

type outcome string

const (
	outcomeComplete   outcome = "complete"
	outcomeTruncated  outcome = "truncated"
	outcomeInvalid    outcome = "invalid_json"
	outcomeDisconnect outcome = "client_disconnect"
	outcomeWriteError outcome = "write_error"
)

// emitter generates the logical body into a bounded chunk buffer and hands
// full chunks to sink. Memory is O(row_bytes + chunk_bytes) for any rows.
type emitter struct {
	ctx      context.Context
	cfg      Config
	pat      *pattern
	buf      []byte
	row      []byte
	sink     func([]byte) error
	hash     hash.Hash
	delay    time.Duration
	progress func()

	sourceBytes int64
	rows        int64 // rows whose final byte was accepted by sink
	pending     int64
	chunks      int64
	writeNs     int64
	delayNs     int64
	err         error
}

func newEmitter(ctx context.Context, cfg Config, sink func([]byte) error) *emitter {
	return &emitter{
		ctx:   ctx,
		cfg:   cfg,
		pat:   shapes[cfg.Shape],
		buf:   make([]byte, 0, cfg.ChunkBytes),
		row:   make([]byte, 0, rowLen(cfg.Rows, cfg.RowBytes)+1),
		sink:  sink,
		hash:  sha256.New(),
		delay: time.Duration(cfg.ChunkDelayMs) * time.Millisecond,
	}
}

func (e *emitter) put(b []byte, endsRow bool) error {
	for len(b) > 0 {
		n := copy(e.buf[len(e.buf):cap(e.buf)], b)
		e.buf = e.buf[:len(e.buf)+n]
		b = b[n:]
		if len(e.buf) == cap(e.buf) {
			if len(b) == 0 && endsRow {
				e.pending++
				endsRow = false
			}
			if err := e.flushChunk(true); err != nil {
				return err
			}
		}
	}
	if endsRow {
		e.pending++
	}
	return nil
}

func (e *emitter) flushChunk(delay bool) error {
	if len(e.buf) == 0 {
		return nil
	}
	if err := e.ctx.Err(); err != nil {
		return err
	}
	t0 := time.Now()
	err := e.sink(e.buf)
	e.writeNs += int64(time.Since(t0))
	if err != nil {
		return err
	}
	e.hash.Write(e.buf)
	e.sourceBytes += int64(len(e.buf))
	e.rows += e.pending
	e.pending = 0
	e.chunks++
	e.buf = e.buf[:0]
	if e.progress != nil {
		e.progress()
	}
	if delay && e.delay > 0 {
		t := time.NewTimer(e.delay)
		t1 := time.Now()
		select {
		case <-t.C:
		case <-e.ctx.Done():
			t.Stop()
			e.delayNs += int64(time.Since(t1))
			return e.ctx.Err()
		}
		e.delayNs += int64(time.Since(t1))
	}
	return nil
}

func (e *emitter) rowAt(i int64) []byte {
	e.row = e.row[:0]
	if i > 0 {
		e.row = append(e.row, ',')
	}
	e.row = appendRow(e.row, e.pat, i, e.cfg.RowBytes)
	return e.row
}

// run emits the whole logical body (or the configured failure prefix) and
// flushes the final partial chunk. It returns the logical outcome or the
// first sink/context error.
func (e *emitter) run() (outcome, error) {
	limit := e.cfg.Rows
	if e.cfg.Failure == "truncate" || e.cfg.Failure == "invalid_json" {
		limit = e.cfg.FailAfterRows
	}
	if err := e.put([]byte{'['}, false); err != nil {
		return "", err
	}
	for i := int64(0); i < limit; i++ {
		if err := e.put(e.rowAt(i), true); err != nil {
			return "", err
		}
	}
	result := outcomeComplete
	switch e.cfg.Failure {
	case "truncate":
		row := e.rowAt(limit)
		if err := e.put(row[:len(row)/2], false); err != nil {
			return "", err
		}
		result = outcomeTruncated
	case "invalid_json":
		e.row = e.row[:0]
		if limit > 0 {
			e.row = append(e.row, ',')
		}
		e.row = append(e.row, `{"i":`...)
		e.row = strconv.AppendInt(e.row, limit, 10)
		e.row = append(e.row, `,"s":NaN}]`...)
		if err := e.put(e.row, false); err != nil {
			return "", err
		}
		result = outcomeInvalid
	default:
		if err := e.put([]byte{']'}, false); err != nil {
			return "", err
		}
	}
	if err := e.flushChunk(false); err != nil {
		return "", err
	}
	return result, nil
}

// expectedBody computes the hash/size/rows of the logical body the configured
// stream emits, with the same generator and no I/O.
func expectedBody(cfg Config) (string, int64, int64) {
	cfg.ChunkDelayMs = 0
	e := newEmitter(context.Background(), cfg, func([]byte) error { return nil })
	if _, err := e.run(); err != nil {
		panic(err)
	}
	return hex.EncodeToString(e.hash.Sum(nil)), e.sourceBytes, e.rows
}

// wireWriter receives post-gzip bytes, splits them per fragment_pattern and
// optionally flushes after every write.
type wireWriter struct {
	w       io.Writer
	rc      *http.ResponseController
	pattern []int
	pi      int
	flush   bool

	bytes, writes int64
	first, last   time.Time
}

func (ww *wireWriter) Write(p []byte) (int, error) {
	total := 0
	for len(p) > 0 {
		n := len(p)
		if len(ww.pattern) > 0 {
			if f := ww.pattern[ww.pi]; f < n {
				n = f
			}
			ww.pi = (ww.pi + 1) % len(ww.pattern)
		}
		m, err := ww.w.Write(p[:n])
		total += m
		ww.bytes += int64(m)
		if err == nil && ww.flush {
			err = ww.rc.Flush()
		}
		if err != nil {
			return total, err
		}
		ww.writes++
		now := time.Now()
		if ww.first.IsZero() {
			ww.first = now
		}
		ww.last = now
		p = p[n:]
	}
	return total, nil
}

func (ww *wireWriter) flushHTTP() error {
	if err := ww.rc.Flush(); err != nil {
		return err
	}
	ww.last = time.Now()
	return nil
}

type requestRecord struct {
	Seq            int64   `json:"seq"`
	Method         string  `json:"method"`
	Path           string  `json:"path"`
	Auth           string  `json:"auth"`
	BodyBytes      int64   `json:"body_bytes"`
	BodySHA256     string  `json:"body_sha256"`
	BodyOverLimit  bool    `json:"body_over_limit"`
	AcceptEncoding string  `json:"accept_encoding"`
	Status         int     `json:"status"`
	Outcome        string  `json:"outcome"`
	Completed      bool    `json:"completed"`
	RowsWritten    int64   `json:"rows_written"`
	SourceBytes    int64   `json:"source_bytes"`
	WireBytes      int64   `json:"wire_bytes"`
	WireWrites     int64   `json:"wire_writes"`
	Chunks         int64   `json:"chunks"`
	WriteErrors    int64   `json:"write_errors"`
	ResponseSHA256 string  `json:"response_sha256"`
	Error          string  `json:"error,omitempty"`
	StartUnixNs    int64   `json:"start_unix_ns"`
	ElapsedMs      float64 `json:"elapsed_ms"`
	FirstWriteUnix int64   `json:"first_body_write_unix_ns"`
	LastWriteUnix  int64   `json:"last_body_write_unix_ns"`
	FirstWriteMs   float64 `json:"first_body_write_ms"`
	LastWriteMs    float64 `json:"last_body_write_ms"`
	GenerateMs     float64 `json:"generate_ms"`
	WriteMs        float64 `json:"write_ms"`
	DelayMs        float64 `json:"delay_ms"`
}

type statsState struct {
	mu                sync.Mutex
	seq               int64
	requests          int64
	queryRequests     int64
	statsRequests     int64
	active            int64
	rowsWritten       int64
	sourceBytes       int64
	wireBytes         int64
	wireWrites        int64
	writeErrors       int64
	completedRequests int64
	last              *requestRecord
	recent            []requestRecord
	recentNext        int

	expectedOnce sync.Once
	expectedHash string
	expectedSize int64
	expectedRows int64
}

func (st *statsState) addProgress(rows, src, wire, writes int64) {
	st.mu.Lock()
	st.rowsWritten += rows
	st.sourceBytes += src
	st.wireBytes += wire
	st.wireWrites += writes
	st.mu.Unlock()
}

func (st *statsState) finish(rec *requestRecord) {
	st.mu.Lock()
	defer st.mu.Unlock()
	st.writeErrors += rec.WriteErrors
	if rec.Completed {
		st.completedRequests++
	}
	if rec.Path == queryPath {
		r := *rec
		st.last = &r
	}
	if len(st.recent) < recentLimit {
		st.recent = append(st.recent, *rec)
	} else {
		st.recent[st.recentNext] = *rec
	}
	st.recentNext = (st.recentNext + 1) % recentLimit
}

type server struct {
	cfg   Config
	start time.Time
	st    statsState
}

func newServer(cfg Config) *server {
	return &server{cfg: cfg, start: time.Now()}
}

func (s *server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == statsPath {
		s.serveStats(w, r)
		return
	}
	s.st.mu.Lock()
	s.st.seq++
	s.st.requests++
	rec := &requestRecord{Seq: s.st.seq, Method: r.Method, Path: r.URL.Path}
	if r.URL.Path == queryPath {
		s.st.queryRequests++
		s.st.active++
	}
	s.st.mu.Unlock()
	t0 := time.Now()
	rec.StartUnixNs = t0.UnixNano()
	rec.AcceptEncoding = r.Header.Get("Accept-Encoding")
	rec.Auth = checkAuth(r)
	finish := func() {
		rec.ElapsedMs = msSince(t0, time.Now())
		s.st.finish(rec)
	}
	if r.URL.Path != queryPath {
		rec.Status, rec.Outcome = http.StatusNotFound, "not_found"
		finish()
		http.NotFound(w, r)
		return
	}
	defer func() {
		s.st.mu.Lock()
		s.st.active--
		s.st.mu.Unlock()
	}()

	h := sha256.New()
	n, err := io.Copy(h, http.MaxBytesReader(w, r.Body, maxRequestBody))
	rec.BodyBytes = n
	if err != nil {
		var mbe *http.MaxBytesError
		if errors.As(err, &mbe) {
			rec.BodyOverLimit = true
			rec.Status, rec.Outcome = http.StatusRequestEntityTooLarge, "body_over_limit"
			finish()
			plain(w, rec.Status, "stress fixture: request body exceeds 1048576 bytes\n")
			return
		}
		rec.Status, rec.Outcome, rec.Error = http.StatusBadRequest, "body_read_error", err.Error()
		finish()
		plain(w, rec.Status, "stress fixture: request body read failed\n")
		return
	}
	rec.BodySHA256 = hex.EncodeToString(h.Sum(nil))

	if r.Method != http.MethodPost {
		rec.Status, rec.Outcome = http.StatusMethodNotAllowed, "method_not_allowed"
		finish()
		w.Header().Set("Allow", http.MethodPost)
		plain(w, rec.Status, "stress fixture: method not allowed\n")
		return
	}
	if rec.Auth != "ok" {
		rec.Status, rec.Outcome = http.StatusUnauthorized, "unauthorized"
		finish()
		w.Header().Set("WWW-Authenticate", `Basic realm="stress-fixture"`)
		plain(w, rec.Status, "stress fixture: unauthorized\n")
		return
	}
	if s.cfg.Failure == "status503" {
		rec.Status, rec.Outcome = http.StatusServiceUnavailable, "status503"
		finish()
		plain(w, rec.Status, status503Body)
		return
	}
	if s.stream(w, r, rec, t0) {
		finish()
		return
	}
	finish()
	// Abort without the terminating chunk: a genuine transport truncation.
	panic(http.ErrAbortHandler)
}

// stream writes the JSON body. It returns false when the connection must be
// aborted to produce a truncation.
func (s *server) stream(w http.ResponseWriter, r *http.Request, rec *requestRecord, t0 time.Time) bool {
	cfg := s.cfg
	hdr := w.Header()
	hdr.Set("Content-Type", "application/json")
	hdr.Set("Cache-Control", "no-store")
	if cfg.Gzip {
		hdr.Set("Content-Encoding", "gzip")
	}
	w.WriteHeader(http.StatusOK)
	rec.Status = http.StatusOK

	ww := &wireWriter{w: w, rc: http.NewResponseController(w), pattern: cfg.FragmentPattern, flush: cfg.Flush}
	var gz *gzip.Writer
	sink := func(b []byte) error { _, err := ww.Write(b); return err }
	if cfg.Gzip {
		gz, _ = gzip.NewWriterLevel(ww, gzip.BestSpeed)
		sink = func(b []byte) error {
			if _, err := gz.Write(b); err != nil {
				return err
			}
			if cfg.Flush {
				return gz.Flush()
			}
			return nil
		}
	}

	e := newEmitter(r.Context(), cfg, sink)
	var lastRows, lastSrc, lastWire, lastWrites int64
	e.progress = func() {
		s.st.addProgress(e.rows-lastRows, e.sourceBytes-lastSrc, ww.bytes-lastWire, ww.writes-lastWrites)
		lastRows, lastSrc, lastWire, lastWrites = e.rows, e.sourceBytes, ww.bytes, ww.writes
	}

	result, err := e.run()
	if err == nil {
		if gz != nil {
			if result == outcomeTruncated {
				err = gz.Flush() // no trailer: the gzip stream is cut too
			} else {
				err = gz.Close()
			}
		}
		if err == nil {
			err = ww.flushHTTP()
		}
	}
	e.progress()

	rec.RowsWritten = e.rows
	rec.SourceBytes = e.sourceBytes
	rec.WireBytes = ww.bytes
	rec.WireWrites = ww.writes
	rec.Chunks = e.chunks
	rec.ResponseSHA256 = hex.EncodeToString(e.hash.Sum(nil))
	if !ww.first.IsZero() {
		rec.FirstWriteUnix = ww.first.UnixNano()
		rec.FirstWriteMs = msSince(t0, ww.first)
	}
	if !ww.last.IsZero() {
		rec.LastWriteUnix = ww.last.UnixNano()
		rec.LastWriteMs = msSince(t0, ww.last)
	}
	total := time.Since(t0)
	rec.WriteMs = float64(e.writeNs) / 1e6
	rec.DelayMs = float64(e.delayNs) / 1e6
	if gen := int64(total) - e.writeNs - e.delayNs; gen > 0 {
		rec.GenerateMs = float64(gen) / 1e6
	}
	if err != nil {
		rec.WriteErrors = 1
		rec.Error = err.Error()
		if r.Context().Err() != nil {
			rec.Outcome = string(outcomeDisconnect)
		} else {
			rec.Outcome = string(outcomeWriteError)
		}
		return true
	}
	rec.Outcome = string(result)
	rec.Completed = result == outcomeComplete
	return result != outcomeTruncated
}

func checkAuth(r *http.Request) string {
	v := r.Header.Get("Authorization")
	if v == "" {
		return "missing"
	}
	scheme, enc, ok := strings.Cut(v, " ")
	if !ok || !strings.EqualFold(scheme, "Basic") {
		return "malformed"
	}
	dec, err := base64.StdEncoding.DecodeString(strings.TrimSpace(enc))
	if err != nil {
		return "malformed"
	}
	user, pass, ok := strings.Cut(string(dec), ":")
	if !ok {
		return "malformed"
	}
	if subtle.ConstantTimeCompare([]byte(user), []byte(authUser)) == 1 &&
		subtle.ConstantTimeCompare([]byte(pass), []byte(authPass)) == 1 {
		return "ok"
	}
	return "invalid"
}

func plain(w http.ResponseWriter, status int, body string) {
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.Header().Set("Content-Length", strconv.Itoa(len(body)))
	w.WriteHeader(status)
	io.WriteString(w, body)
}

func msSince(t0, t time.Time) float64 {
	return float64(t.Sub(t0)) / 1e6
}

type statsResponse struct {
	Requests          int64           `json:"requests"`
	QueryRequests     int64           `json:"query_requests"`
	StatsRequests     int64           `json:"stats_requests"`
	ActiveQueries     int64           `json:"active_queries"`
	RowsWritten       int64           `json:"rows_written"`
	SourceBytes       int64           `json:"source_bytes"`
	WireBytes         int64           `json:"wire_bytes"`
	WireWrites        int64           `json:"wire_writes"`
	WriteErrors       int64           `json:"write_errors"`
	CompletedRequests int64           `json:"completed_requests"`
	Completed         bool            `json:"completed"`
	ResponseSHA256    string          `json:"response_sha256"`
	ExpectedSHA256    string          `json:"expected_sha256,omitempty"`
	ExpectedBytes     *int64          `json:"expected_source_bytes,omitempty"`
	ExpectedRows      *int64          `json:"expected_rows,omitempty"`
	ElapsedMs         float64         `json:"elapsed_ms"`
	CPUUserMs         float64         `json:"cpu_user_ms"`
	CPUSysMs          float64         `json:"cpu_sys_ms"`
	CPUMs             float64         `json:"cpu_ms"`
	MaxRSSKiB         int64           `json:"max_rss_kib"`
	Config            Config          `json:"config"`
	Limits            map[string]int  `json:"limits"`
	LastQuery         *requestRecord  `json:"last_query"`
	Recent            []requestRecord `json:"recent"`
}

func (s *server) serveStats(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		plain(w, http.StatusMethodNotAllowed, "stress fixture: method not allowed\n")
		return
	}
	st := &s.st
	if r.URL.Query().Get("expected") == "1" {
		st.expectedOnce.Do(func() {
			st.expectedHash, st.expectedSize, st.expectedRows = expectedBody(s.cfg)
		})
	}
	st.mu.Lock()
	st.statsRequests++
	resp := statsResponse{
		Requests:          st.requests,
		QueryRequests:     st.queryRequests,
		StatsRequests:     st.statsRequests,
		ActiveQueries:     st.active,
		RowsWritten:       st.rowsWritten,
		SourceBytes:       st.sourceBytes,
		WireBytes:         st.wireBytes,
		WireWrites:        st.wireWrites,
		WriteErrors:       st.writeErrors,
		CompletedRequests: st.completedRequests,
		ElapsedMs:         msSince(s.start, time.Now()),
		Config:            s.cfg,
		Limits: map[string]int{
			"max_row_bytes":        maxRowBytes,
			"max_chunk_bytes":      maxChunkBytes,
			"max_request_body":     maxRequestBody,
			"max_fragment_entries": maxFragmentEntries,
			"max_fragment_size":    maxFragmentSize,
			"max_chunk_delay_ms":   maxDelayMs,
			"recent_limit":         recentLimit,
		},
		Recent: make([]requestRecord, 0, len(st.recent)),
	}
	if st.expectedHash != "" {
		resp.ExpectedSHA256 = st.expectedHash
		resp.ExpectedBytes = &st.expectedSize
		resp.ExpectedRows = &st.expectedRows
	}
	if st.last != nil {
		last := *st.last
		resp.LastQuery = &last
		resp.Completed = last.Completed
		resp.ResponseSHA256 = last.ResponseSHA256
	}
	if len(st.recent) < recentLimit {
		resp.Recent = append(resp.Recent, st.recent...)
	} else {
		resp.Recent = append(resp.Recent, st.recent[st.recentNext:]...)
		resp.Recent = append(resp.Recent, st.recent[:st.recentNext]...)
	}
	st.mu.Unlock()

	var ru syscall.Rusage
	if syscall.Getrusage(syscall.RUSAGE_SELF, &ru) == nil {
		resp.CPUUserMs = float64(ru.Utime.Nano()) / 1e6
		resp.CPUSysMs = float64(ru.Stime.Nano()) / 1e6
		resp.CPUMs = resp.CPUUserMs + resp.CPUSysMs
		resp.MaxRSSKiB = int64(ru.Maxrss)
	}
	body, _ := json.Marshal(resp)
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Length", strconv.Itoa(len(body)+1))
	w.Write(append(body, '\n'))
}

func readyLine(addr string, pid int) []byte {
	line, _ := json.Marshal(struct {
		URL string `json:"url"`
		PID int    `json:"pid"`
	}{"http://" + addr, pid})
	return append(line, '\n')
}

func run(ctx context.Context, args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("pb-stress-fixture", flag.ContinueOnError)
	fs.SetOutput(stderr)
	configPath := fs.String("config", "", "path to JSON config")
	printExpected := fs.Bool("print-expected", false, "print the expected logical body hash and exit")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if *configPath == "" || fs.NArg() != 0 {
		fmt.Fprintln(stderr, "usage: pb-stress-fixture --config PATH [--print-expected]")
		return 2
	}
	f, err := os.Open(*configPath)
	if err != nil {
		fmt.Fprintln(stderr, "stress fixture:", err)
		return 2
	}
	data, err := io.ReadAll(io.LimitReader(f, maxConfigBytes+1))
	f.Close()
	if err != nil {
		fmt.Fprintln(stderr, "stress fixture:", err)
		return 2
	}
	cfg, err := parseConfig(data)
	if err != nil {
		fmt.Fprintln(stderr, "stress fixture:", err)
		return 2
	}
	if *printExpected {
		sum, size, rows := expectedBody(cfg)
		out, _ := json.Marshal(map[string]any{"expected_sha256": sum, "expected_source_bytes": size, "expected_rows": rows})
		fmt.Fprintf(stdout, "%s\n", out)
		return 0
	}

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		fmt.Fprintln(stderr, "stress fixture:", err)
		return 1
	}
	baseCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	srv := &http.Server{
		Handler:           newServer(cfg),
		ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout:       60 * time.Second,
		MaxHeaderBytes:    64 << 10,
		BaseContext:       func(net.Listener) context.Context { return baseCtx },
		ErrorLog:          nil,
	}
	errc := make(chan error, 1)
	go func() { errc <- srv.Serve(ln) }()
	out := bufio.NewWriter(stdout)
	out.Write(readyLine(ln.Addr().String(), os.Getpid()))
	out.Flush()

	select {
	case err := <-errc:
		fmt.Fprintln(stderr, "stress fixture:", err)
		return 1
	case <-ctx.Done():
	}
	cancel() // cancels in-flight stream contexts (delays, writes)
	sctx, scancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer scancel()
	if srv.Shutdown(sctx) != nil {
		srv.Close()
	}
	return 0
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	os.Exit(run(ctx, os.Args[1:], os.Stdout, os.Stderr))
}
