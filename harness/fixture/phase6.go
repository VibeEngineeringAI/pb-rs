// Phase-six live-tail fixture: scripted Arrow Flight subscriptions, conditional
// HTTP routes, TLS, bounded event capture and a control API. It is enabled only
// by a scenario "phase6" object; legacy scenarios never reach this file.
package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"hash"
	"io"
	"math"
	"math/big"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/apache/arrow/go/v13/arrow"
	"github.com/apache/arrow/go/v13/arrow/array"
	"github.com/apache/arrow/go/v13/arrow/decimal128"
	"github.com/apache/arrow/go/v13/arrow/decimal256"
	"github.com/apache/arrow/go/v13/arrow/flight"
	"github.com/apache/arrow/go/v13/arrow/float16"
	"github.com/apache/arrow/go/v13/arrow/ipc"
	"github.com/apache/arrow/go/v13/arrow/memory"
	"golang.org/x/net/http2"
	"golang.org/x/net/http2/h2c"
	"golang.org/x/net/http2/hpack"
	"golang.org/x/sys/unix"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/keepalive"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/peer"
	"google.golang.org/grpc/stats"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"
)

// Scenario schema. See docs/phase6-fixture-notes.md.
type p6Config struct {
	EventsPath       string               `json:"events_path"`
	SummaryPath      string               `json:"summary_path"`
	EventLimit       int                  `json:"event_limit"`
	EventHardLimit   int                  `json:"event_hard_limit"`
	BatchEventSample int                  `json:"batch_event_sample"`
	EventSample      int                  `json:"event_sample"`
	TraceLimit       int                  `json:"trace_limit"`
	ExpectedOutput   bool                 `json:"expected_output"`
	Listen           string               `json:"listen"`
	TLS              *p6TLS               `json:"tls"`
	Keepalive        *p6Keepalive         `json:"keepalive"`
	FlightAuth       map[string]string    `json:"flight_auth"`
	HTTP             []*p6Route           `json:"http"`
	Streams          map[string]*p6Stream `json:"streams"`
	Endpoints        []*p6Endpoint        `json:"endpoints"`
}

// p6Endpoint is an extra listener for protocol-mismatch probes: "blackhole"
// accepts (optionally completing TLS) but never speaks HTTP/2; "http_status"
// answers every HTTP/2 request with a fixed non-gRPC response.
type p6Endpoint struct {
	Name        string `json:"name"`
	Kind        string `json:"kind"`
	TLS         bool   `json:"tls"`
	HoldMS      int    `json:"hold_ms"`
	Status      int    `json:"status"`
	ContentType string `json:"content_type"`
	Body        string `json:"body"`
	// raw_h2 only: a scripted HTTP/2 server whose frames are not sanitized.
	Schema        string     `json:"schema"`
	Subscriptions []p6RawSub `json:"subscriptions"`
	next          int64
}

type p6RawSub struct {
	Repeat int     `json:"repeat"`
	Acts   []p6Act `json:"acts"`
}

type p6Act struct {
	Act       string          `json:"act"`
	Fields    [][2]string     `json:"fields"`
	EndStream bool            `json:"end_stream"`
	Rows      int             `json:"rows"`
	B64       string          `json:"b64"`
	Code      uint32          `json:"code"`
	Last      json.RawMessage `json:"last"`
	Debug     string          `json:"debug"`
	MS        int             `json:"ms"`
}

func (a *p6Act) terminal() bool {
	switch a.Act {
	case "trailers", "end_stream", "rst", "close":
		return true
	case "headers", "data":
		return a.EndStream
	}
	return false
}

func (a *p6Act) validate() error {
	switch a.Act {
	case "headers", "trailers", "schema", "end_stream", "rst", "close", "sleep":
	case "batch":
		if a.Rows < 0 {
			return errors.New("negative rows")
		}
	case "data":
		if _, err := base64.StdEncoding.DecodeString(a.B64); err != nil {
			return err
		}
	case "goaway":
		var v any
		if len(a.Last) > 0 {
			if err := json.Unmarshal(a.Last, &v); err != nil {
				return err
			}
		}
		switch x := v.(type) {
		case nil, float64:
		case string:
			if x != "below" && x != "ours" {
				return fmt.Errorf("goaway last %q", x)
			}
		default:
			return fmt.Errorf("goaway last %v", v)
		}
	default:
		return fmt.Errorf("unknown act %q", a.Act)
	}
	return nil
}

type p6TLS struct {
	GRPC       bool     `json:"grpc"`
	HTTP       bool     `json:"http"`
	CAPath     string   `json:"ca_path"`
	Names      []string `json:"names"`
	MaxVersion string   `json:"max_version"`
	MinVersion string   `json:"min_version"`
	NoALPN     bool     `json:"no_alpn"`
	// ResetInvalidHandshake aborts (SO_LINGER=0, RST) a gRPC connection whose
	// TLS handshake failed, instead of a close whose FIN/RST depends on unread
	// client bytes.
	ResetInvalidHandshake bool `json:"reset_invalid_handshake"`
}

type p6Keepalive struct {
	TimeMS          int `json:"time_ms"`
	TimeoutMS       int `json:"timeout_ms"`
	MinClientPingMS int `json:"min_client_ping_ms"`
}

type p6Route struct {
	Method    string        `json:"method"`
	Path      string        `json:"path"`
	Responses []*p6Response `json:"responses"`
}

type p6Response struct {
	Require map[string]string `json:"require"`
	Status  int               `json:"status"`
	Body    string            `json:"body"`
	Headers map[string]string `json:"headers"`
	Times   int               `json:"times"`
	DelayMS int               `json:"delay_ms"`
	Gate    string            `json:"gate"`
	used    int
}

type p6Dictionary struct {
	Cardinality int    `json:"cardinality"`
	Mode        string `json:"mode"`
	ValueBytes  int    `json:"value_bytes"`
	MaxEntries  int    `json:"max_entries"`
}

type p6Stream struct {
	Schema        string           `json:"schema"`
	Compression   string           `json:"compression"`
	PayloadBytes  int              `json:"payload_bytes"`
	Seed          int64            `json:"seed"`
	Dictionary    *p6Dictionary    `json:"dictionary"`
	Subscriptions []p6Subscription `json:"subscriptions"`
	// SummaryBucketMS is the width of the per-stream summary time buckets.
	SummaryBucketMS int `json:"summary_bucket_ms"`
	// ExpectedTicket defaults to Go json.Marshal({"stream": NAME}).
	ExpectedTicket *string `json:"expected_ticket"`
}

type p6Subscription struct {
	Repeat int    `json:"repeat"`
	Ops    []p6Op `json:"ops"`
}

type p6Op struct {
	Op                 string `json:"op"`
	Schema             string `json:"schema"`
	Rows               int    `json:"rows"`
	Count              int    `json:"count"`
	PayloadBytes       *int   `json:"payload_bytes"`
	TargetMessageBytes int    `json:"target_message_bytes"`
	RowsPerBatch       int    `json:"rows_per_batch"`
	Batches            int    `json:"batches"`
	IntervalMS         int    `json:"interval_ms"`
	DurationMS         int    `json:"duration_ms"`
	MS                 int    `json:"ms"`
	Name               string `json:"name"`
	Code               int    `json:"code"`
	Message            string `json:"message"`
	AppMetadata        string `json:"app_metadata"`
	HeaderB64          string `json:"header_b64"`
	BodyB64            string `json:"body_b64"`
	AppMetadataB64     string `json:"app_metadata_b64"`
	Kind               string `json:"kind"`
	MessageBytes       int    `json:"message_bytes"`
	TrailerPadBytes    int    `json:"trailer_pad_bytes"`
	SettleMS           *int   `json:"settle_ms"`
	LastStreamID       uint32 `json:"last_stream_id"`
	ErrorCode          uint32 `json:"error_code"`
	Debug              string `json:"debug"`
	CloseAfterMS       int    `json:"close_after_ms"`
}

var p6Kinds = map[string]bool{
	"basic": true, "wide": true, "values": true, "dup": true, "nan": true, "dictionary": true,
	"empty": true, "union": true, "ree": true, "invalid_utf8": true,
}

var p6CorruptKinds = map[string]bool{
	"garbage_header": true, "truncated_body": true, "empty_message": true, "foreign_batch": true,
	"dictionary_before_schema": true, "missing_dictionary": true, "dictionary_only": true,
}

func (c *p6Config) validate() error {
	if c.TLS != nil && (c.TLS.GRPC || c.TLS.HTTP) && c.TLS.CAPath == "" {
		return errors.New("phase6.tls.ca_path is required")
	}
	names := map[string]bool{}
	for _, e := range c.Endpoints {
		if e.Name == "" || names[e.Name] || (e.Kind != "blackhole" && e.Kind != "http_status" && e.Kind != "raw_h2") {
			return fmt.Errorf("phase6.endpoints entry needs a unique name and kind blackhole|http_status|raw_h2: %+v", e)
		}
		if e.Kind == "raw_h2" {
			if e.Schema == "" {
				e.Schema = "basic"
			}
			if !p6Kinds[e.Schema] {
				return fmt.Errorf("endpoint %q: unknown schema %q", e.Name, e.Schema)
			}
			for i, sub := range e.Subscriptions {
				if len(sub.Acts) == 0 || !sub.Acts[len(sub.Acts)-1].terminal() {
					return fmt.Errorf("endpoint %q subscription %d must end with a terminal act", e.Name, i)
				}
				for j := range sub.Acts {
					if err := sub.Acts[j].validate(); err != nil {
						return fmt.Errorf("endpoint %q subscription %d act %d: %w", e.Name, i, j, err)
					}
				}
			}
		}
		names[e.Name] = true
		if e.TLS && (c.TLS == nil || c.TLS.CAPath == "") {
			return fmt.Errorf("endpoint %q: tls requires phase6.tls.ca_path", e.Name)
		}
	}
	for _, route := range c.HTTP {
		if route.Method == "" || route.Path == "" || len(route.Responses) == 0 {
			return fmt.Errorf("phase6.http route needs method, path and responses: %+v", route)
		}
	}
	for name, s := range c.Streams {
		if s.Schema == "" {
			s.Schema = "basic"
		}
		if !p6Kinds[s.Schema] {
			return fmt.Errorf("stream %q: unknown schema %q", name, s.Schema)
		}
		switch s.Compression {
		case "", "lz4", "zstd":
		default:
			return fmt.Errorf("stream %q: unknown compression %q", name, s.Compression)
		}
		if s.SummaryBucketMS < 0 || int64(s.SummaryBucketMS) > math.MaxInt64/int64(time.Millisecond) {
			return fmt.Errorf("stream %q: invalid summary_bucket_ms", name)
		}
		if s.Dictionary != nil {
			if s.Dictionary.Mode == "cycle" && (s.Dictionary.Cardinality < 0 || s.Dictionary.MaxEntries < 0 || s.Dictionary.ValueBytes < 0) {
				return fmt.Errorf("stream %q: negative cycle dictionary option", name)
			}
			if s.Dictionary.Mode == "cycle" {
				card, maxEntries := s.Dictionary.Cardinality, s.Dictionary.MaxEntries
				if card == 0 {
					card = 16
				}
				if maxEntries == 0 {
					maxEntries = 4096
				}
				if card > maxEntries {
					return fmt.Errorf("stream %q: cycle cardinality exceeds max_entries", name)
				}
			}
			switch s.Dictionary.Mode {
			case "", "stable", "replace", "delta", "cycle":
			default:
				return fmt.Errorf("stream %q: unknown dictionary mode %q", name, s.Dictionary.Mode)
			}
		}
		for i, sub := range s.Subscriptions {
			if sub.Repeat < 0 {
				return fmt.Errorf("stream %q subscription %d: negative repeat", name, i)
			}
			for j, op := range sub.Ops {
				if err := op.validate(); err != nil {
					return fmt.Errorf("stream %q subscription %d op %d: %w", name, i, j, err)
				}
			}
		}
	}
	return nil
}

func (op *p6Op) validate() error {
	switch op.Op {
	case "schema", "foreign_schema":
		if op.Schema != "" && !p6Kinds[op.Schema] {
			return fmt.Errorf("unknown schema %q", op.Schema)
		}
	case "batch":
		if op.Rows < 0 || op.Count < 0 {
			return errors.New("negative rows/count")
		}
	case "live":
		if op.Batches <= 0 && op.DurationMS <= 0 {
			return errors.New("live requires batches or duration_ms")
		}
	case "sleep", "hold", "eof", "headers", "metadata_only", "conn_close", "conn_reset", "goaway":
	case "gate":
		if op.Name == "" {
			return errors.New("gate requires name")
		}
	case "status":
		if op.MessageBytes < 0 || op.TrailerPadBytes < 0 {
			return errors.New("negative message_bytes/trailer_pad_bytes")
		}
		if op.Code < 0 || op.Code > 16 {
			return fmt.Errorf("status code %d outside 0..16", op.Code)
		}
	case "raw":
		for _, v := range []string{op.HeaderB64, op.BodyB64, op.AppMetadataB64} {
			if _, err := base64.StdEncoding.DecodeString(v); err != nil {
				return fmt.Errorf("raw base64: %w", err)
			}
		}
	case "corrupt":
		if !p6CorruptKinds[op.Kind] {
			return fmt.Errorf("unknown corrupt kind %q", op.Kind)
		}
	default:
		return fmt.Errorf("unknown op %q", op.Op)
	}
	return nil
}

// ops returns the script for a stream's nth DoGet, or nil when exhausted.
func (s *p6Stream) ops(n int64) []p6Op {
	for _, sub := range s.Subscriptions {
		repeat := int64(max(sub.Repeat, 1))
		if n < repeat {
			return sub.Ops
		}
		n -= repeat
	}
	return nil
}

// Runtime state.

type p6Counter struct {
	Count int64           `json:"count"`
	Last  json.RawMessage `json:"event"`
}

type p6Gate struct {
	tokens, waiting, released int64
	notify                    chan struct{}
}

type p6Terminal struct {
	Code    string `json:"code"`
	Message string `json:"message"`
	Reason  string `json:"reason"`
	Sub     int64  `json:"sub"`
	TNS     int64  `json:"t_ns"`
}

type p6StreamStats struct {
	DoGets                int64            `json:"dogets"`
	Subscriptions         int64            `json:"subscriptions"`
	Batches               int64            `json:"batches"`
	ZeroRowBatches        int64            `json:"zero_row_batches"`
	Rows                  int64            `json:"rows"`
	Messages              int64            `json:"messages"`
	EncodedBytes          int64            `json:"encoded_bytes"`
	DecodedBytes          int64            `json:"decoded_bytes"`
	GRPCWireBytes         int64            `json:"grpc_wire_bytes"`
	MaxMessageBytes       int64            `json:"max_message_bytes"`
	ExpectedStdoutSHA256  *string          `json:"expected_stdout_sha256"`
	ExpectedStdoutBytes   *int64           `json:"expected_stdout_bytes"`
	ExpectedRows          int64            `json:"expected_rows"`
	ExpectedRenderedLines *int64           `json:"expected_rendered_rows"`
	ExpectedOutputNS      int64            `json:"expected_output_ns"`
	NextSequence          int64            `json:"next_sequence"`
	Terminals             map[string]int64 `json:"terminals"`
	LastTerminal          *p6Terminal      `json:"last_terminal"`
	Authorities           map[string]int64 `json:"authorities"`
	Connections           int64            `json:"connections,omitempty"` // raw_h2 endpoints only
	FirstDoGetNS          *int64           `json:"first_doget_ns"`
	LastDoGetNS           *int64           `json:"last_doget_ns"`
	FirstNonemptyBatchNS  *int64           `json:"first_nonempty_batch_emit_ns"`
	RequestValidation     p6Validation     `json:"request_validation"`
	ReconnectGapNS        p6Gap            `json:"reconnect_gap_ns"`
	Live                  *p6Live          `json:"live"`
	DictionaryCounts      *p6DictCounts    `json:"dictionary"`
	Buckets               *p6Buckets       `json:"buckets"`
	BatchSendCallNS       p6Agg            `json:"batch_send_call_ns"`
	hash                  hash.Hash
	stdoutBytes           int64
	renderedLines         int64
}

type phase6 struct {
	cfg            *p6Config
	mu             sync.Mutex
	file           *os.File
	events         *bufio.Writer
	seq            int64
	written        int64
	dropped        int64
	kinds          map[string]int64
	waits          map[string]*p6Counter
	ring           []json.RawMessage
	changed        chan struct{}
	gates          map[string]*p6Gate
	streams        map[string]*p6StreamStats
	dogets         int64
	traceDropped   atomic.Int64
	conns          map[string]*p6Conn
	httpRequests   int64
	aboutRequests  int64
	firstAbout     *int64
	lastAbout      *int64
	aboutCompleted *int64
	startNS        int64
	ports          map[string]int
	caPEM          []byte
	tlsCert        *tls.Certificate
	stop           chan struct{}
	closed         sync.Once
}

const p6RingSize = 1024

func newPhase6(cfg *p6Config) (*phase6, error) {
	if err := cfg.validate(); err != nil {
		return nil, err
	}
	if cfg.EventLimit <= 0 {
		cfg.EventLimit = 20000
	}
	if cfg.EventHardLimit <= 0 {
		cfg.EventHardLimit = 10 * cfg.EventLimit
	}
	if cfg.BatchEventSample <= 0 {
		cfg.BatchEventSample = 100
	}
	if cfg.EventSample <= 0 {
		cfg.EventSample = 10
	}
	if cfg.TraceLimit <= 0 {
		cfg.TraceLimit = 4096
	}
	p := &phase6{
		startNS: monotonicNS(), cfg: cfg, kinds: map[string]int64{}, waits: map[string]*p6Counter{}, changed: make(chan struct{}),
		gates: map[string]*p6Gate{}, streams: map[string]*p6StreamStats{}, stop: make(chan struct{}),
		conns: map[string]*p6Conn{}, ports: map[string]int{},
	}
	for name, spec := range cfg.Streams {
		p.streams[name] = p.newStats()
		if spec.Schema == "dictionary" {
			p.streams[name].DictionaryCounts = &p6DictCounts{}
		}
	}
	if cfg.EventsPath != "" {
		file, err := os.Create(cfg.EventsPath)
		if err != nil {
			return nil, err
		}
		p.file, p.events = file, bufio.NewWriterSize(file, 64<<10)
	}
	return p, nil
}

func (p *phase6) newStats() *p6StreamStats {
	s := &p6StreamStats{Terminals: map[string]int64{}, Authorities: map[string]int64{}}
	if p.cfg.ExpectedOutput {
		s.hash = sha256.New()
	}
	return s
}

func (p *phase6) listenHost() string {
	if p.cfg.Listen != "" {
		return p.cfg.Listen
	}
	return "127.0.0.1"
}

// start configures TLS/keepalive/stats, the control listener and periodic
// flushing. It returns gRPC server options and the (possibly TLS) HTTP listener.
// The gRPC listener is wrapped so script ops can close, reset or write frames to
// the connection carrying a stream.
func (p *phase6) start(f *fixture, banner map[string]any, httpListener, grpcListener net.Listener) ([]grpc.ServerOption, net.Listener, net.Listener, error) {
	options, httpListener, err := p.startServers(f, banner, httpListener)
	return options, httpListener, &p6Listener{Listener: grpcListener, p: p}, err
}

func (p *phase6) startServers(f *fixture, banner map[string]any, httpListener net.Listener) ([]grpc.ServerOption, net.Listener, error) {
	options := []grpc.ServerOption{grpc.StatsHandler(&p6Stats{p: p})}
	banner["grpc_tls"], banner["http_tls"] = false, false
	if t := p.cfg.TLS; t != nil && t.CAPath != "" {
		if err := p.generateTLS(); err != nil {
			return nil, nil, err
		}
		if err := os.WriteFile(t.CAPath, p.caPEM, 0o644); err != nil {
			return nil, nil, err
		}
		banner["ca_path"] = t.CAPath
		if t.GRPC {
			cfg := p.tlsConfig([]string{"h2"})
			options = append(options, grpc.Creds(&p6Creds{p: p, cfg: cfg}))
			banner["grpc_tls"] = true
		}
		if t.HTTP {
			httpListener = &p6TLSListener{Listener: httpListener, p: p, cfg: p.tlsConfig([]string{"http/1.1"})}
			banner["url"] = "https://" + httpListener.Addr().String()
			banner["http_tls"] = true
		}
	}
	if k := p.cfg.Keepalive; k != nil {
		params := keepalive.ServerParameters{}
		if k.TimeMS > 0 {
			params.Time = time.Duration(k.TimeMS) * time.Millisecond
		}
		if k.TimeoutMS > 0 {
			params.Timeout = time.Duration(k.TimeoutMS) * time.Millisecond
		}
		minPing := k.MinClientPingMS
		if minPing <= 0 {
			minPing = 1000
		}
		options = append(options, grpc.KeepaliveParams(params),
			grpc.KeepaliveEnforcementPolicy(keepalive.EnforcementPolicy{MinTime: time.Duration(minPing) * time.Millisecond, PermitWithoutStream: true}))
	}
	control, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, nil, err
	}
	banner["control_url"] = "http://" + control.Addr().String()
	go func() {
		_ = http.Serve(control, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if !strings.HasPrefix(r.URL.Path, "/__harness/") {
				http.NotFound(w, r)
				return
			}
			f.ServeHTTP(w, r)
		}))
	}()
	if len(p.cfg.Endpoints) > 0 {
		ports, err := p.startEndpoints(f)
		if err != nil {
			return nil, nil, err
		}
		banner["endpoints"] = ports
	}
	p.writeSummary() // exists from readiness on, even if the fixture is killed early
	go p.flushLoop()
	return options, httpListener, nil
}

func (p *phase6) flushLoop() {
	flush := time.NewTicker(100 * time.Millisecond)
	defer flush.Stop()
	summary := time.NewTicker(time.Second)
	defer summary.Stop()
	for {
		select {
		case <-p.stop:
			return
		case <-flush.C:
			p.mu.Lock()
			p.flushLocked()
			p.mu.Unlock()
		case <-summary.C:
			p.writeSummary()
		}
	}
}

func (p *phase6) close() {
	p.closed.Do(func() {
		close(p.stop)
		p.writeSummary()
		p.mu.Lock()
		defer p.mu.Unlock()
		p.flushLocked()
		if p.file != nil {
			_ = p.file.Close()
		}
	})
}

func (p *phase6) flushLocked() {
	if p.events != nil {
		_ = p.events.Flush()
	}
}

// Events.

type p6Fields map[string]any

func isBatchEvent(kind string) bool { return strings.HasPrefix(kind, "batch_") }

// emit records one event with an explicit CLOCK_MONOTONIC timestamp. Disk output
// is bounded: all events up to event_limit, then a per-kind sample up to
// event_hard_limit, then nothing. Counters and wait keys stay exact.
func (p *phase6) emit(t int64, kind string, fields p6Fields) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.seq++
	if fields == nil {
		fields = p6Fields{}
	}
	fields["seq"], fields["t_ns"], fields["kind"] = p.seq, t, kind
	raw, err := json.Marshal(fields)
	if err != nil {
		raw, _ = json.Marshal(p6Fields{"seq": p.seq, "t_ns": t, "kind": kind, "marshal_error": err.Error()})
	}
	p.kinds[kind]++
	stream, _ := fields["stream"].(string)
	name, _ := fields["name"].(string)
	for _, key := range []string{kind, kind + "|s=" + stream, kind + "|n=" + name, kind + "|s=" + stream + "|n=" + name} {
		c := p.waits[key]
		if c == nil {
			c = &p6Counter{}
			p.waits[key] = c
		}
		c.Count++
		c.Last = raw
	}
	if len(p.ring) < p6RingSize {
		p.ring = append(p.ring, raw)
	} else {
		p.ring[(p.seq-1)%p6RingSize] = raw
	}
	write := p.written < int64(p.cfg.EventLimit)
	if !write && p.written < int64(p.cfg.EventHardLimit) {
		sample := int64(p.cfg.EventSample)
		if isBatchEvent(kind) {
			sample = int64(p.cfg.BatchEventSample)
		}
		write = p.kinds[kind]%sample == 0
	}
	if write && p.events != nil {
		p.written++
		_, _ = p.events.Write(raw)
		_ = p.events.WriteByte('\n')
		if !isBatchEvent(kind) {
			p.flushLocked()
		}
	} else if !write {
		p.dropped++
	}
	close(p.changed)
	p.changed = make(chan struct{})
}

func (p *phase6) stats(stream string) *p6StreamStats {
	s := p.streams[stream]
	if s == nil {
		s = p.newStats()
		p.streams[stream] = s
	}
	return s
}

// p6Validation counts every DoGet exactly, independent of event retention.
type p6Validation struct {
	AuthChecks         int64 `json:"auth_checks"`
	InvalidAuth        int64 `json:"invalid_auth"`
	TicketChecks       int64 `json:"ticket_checks"`
	TicketMismatches   int64 `json:"ticket_mismatches"`
	UnexpectedRequests int64 `json:"unexpected_requests"`
	DoGetBeforeAbout   int64 `json:"doget_before_about"`
}

// p6Gap aggregates previous-terminal to next-DoGet gaps (server-side clock).
type p6Gap struct {
	Count int64  `json:"count"`
	Min   *int64 `json:"min"`
	Max   *int64 `json:"max"`
	Sum   int64  `json:"sum"`
}

func (g *p6Gap) add(ns int64) {
	g.Count++
	g.Sum += ns
	if g.Min == nil || ns < *g.Min {
		g.Min = &ns
	}
	if g.Max == nil || ns > *g.Max {
		v := ns
		g.Max = &v
	}
}

type p6Summary struct {
	HTTPRequests     int64                       `json:"http_requests"`
	AboutRequests    int64                       `json:"about_requests"`
	FirstAboutNS     *int64                      `json:"first_about_ns"`
	LastAboutNS      *int64                      `json:"last_about_ns"`
	AboutCompletedNS *int64                      `json:"about_completed_ns"`
	Streams          map[string]*p6StreamStats   `json:"streams"`
	DoGets           int64                       `json:"dogets"`
	EventsTotal      int64                       `json:"events_total"`
	EventsWritten    int64                       `json:"events_written"`
	EventsDropped    int64                       `json:"events_dropped"`
	EventKinds       map[string]int64            `json:"event_kinds"`
	TraceDropped     int64                       `json:"trace_dropped"`
	Gates            map[string]map[string]int64 `json:"gates"`
	FixtureCPUUserNS int64                       `json:"fixture_cpu_user_ns"`
	FixtureCPUSysNS  int64                       `json:"fixture_cpu_system_ns"`
	FixtureStartNS   int64                       `json:"fixture_start_ns"`
	ExpectedOutputNS int64                       `json:"expected_output_ns_total"`
	MonotonicNS      int64                       `json:"t_ns"`
}

func (p *phase6) summary() ([]byte, error) {
	var usage unix.Rusage
	_ = unix.Getrusage(unix.RUSAGE_SELF, &usage)
	p.mu.Lock()
	defer p.mu.Unlock()
	for _, s := range p.streams {
		if s.hash != nil {
			sum := hex.EncodeToString(s.hash.Sum(nil))
			n, lines := s.stdoutBytes, s.renderedLines
			s.ExpectedStdoutSHA256, s.ExpectedStdoutBytes, s.ExpectedRenderedLines = &sum, &n, &lines
		}
	}
	expectedNS := int64(0)
	for _, s := range p.streams {
		expectedNS += s.ExpectedOutputNS
	}
	gates := map[string]map[string]int64{}
	for name, g := range p.gates {
		gates[name] = map[string]int64{"waiting": g.waiting, "released": g.released, "tokens": g.tokens}
	}
	return json.Marshal(p6Summary{
		HTTPRequests: p.httpRequests, AboutRequests: p.aboutRequests, FirstAboutNS: p.firstAbout,
		LastAboutNS: p.lastAbout, AboutCompletedNS: p.aboutCompleted, Streams: p.streams, DoGets: p.dogets, EventsTotal: p.seq, EventsWritten: p.written,
		EventsDropped: p.dropped, EventKinds: p.kinds, TraceDropped: p.traceDropped.Load(), Gates: gates,
		FixtureCPUUserNS: usage.Utime.Nano(), FixtureCPUSysNS: usage.Stime.Nano(), MonotonicNS: monotonicNS(),
		FixtureStartNS: p.startNS, ExpectedOutputNS: expectedNS,
	})
}

func (p *phase6) writeSummary() {
	if p.cfg.SummaryPath == "" {
		return
	}
	data, err := p.summary()
	if err != nil {
		return
	}
	tmp := p.cfg.SummaryPath + ".tmp"
	if os.WriteFile(tmp, append(data, '\n'), 0o644) == nil {
		_ = os.Rename(tmp, p.cfg.SummaryPath)
	}
}

// Gates: counting semaphores; a release before the wait is remembered.

func (p *phase6) gate(name string) *p6Gate {
	g := p.gates[name]
	if g == nil {
		g = &p6Gate{notify: make(chan struct{})}
		p.gates[name] = g
	}
	return g
}

func (p *phase6) waitGate(ctx context.Context, name string) bool {
	p.mu.Lock()
	g := p.gate(name)
	g.waiting++
	defer func() {
		p.mu.Lock()
		g.waiting--
		p.mu.Unlock()
	}()
	for {
		if g.tokens > 0 {
			g.tokens--
			p.mu.Unlock()
			return true
		}
		ch := g.notify
		p.mu.Unlock()
		select {
		case <-ctx.Done():
			return false
		case <-ch:
		}
		p.mu.Lock()
	}
}

func (p *phase6) releaseGate(name string, n int64) {
	p.mu.Lock()
	g := p.gate(name)
	g.tokens += n
	g.released += n
	close(g.notify)
	g.notify = make(chan struct{})
	p.mu.Unlock()
	p.emit(monotonicNS(), "gate_release", p6Fields{"name": name, "n": n})
}

// Control HTTP API.

func (p *phase6) serveControl(w http.ResponseWriter, r *http.Request) bool {
	if !strings.HasPrefix(r.URL.Path, "/__harness/phase6/") {
		return false
	}
	q := r.URL.Query()
	w.Header().Set("Content-Type", "application/json")
	switch strings.TrimPrefix(r.URL.Path, "/__harness/phase6/") {
	case "summary":
		data, err := p.summary()
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return true
		}
		_, _ = w.Write(data)
	case "events":
		since, _ := strconv.ParseInt(q.Get("since"), 10, 64)
		p.mu.Lock()
		out := []json.RawMessage{}
		first := max(p.seq-int64(len(p.ring))+1, since+1)
		for s := first; s <= p.seq; s++ {
			out = append(out, p.ring[(s-1)%p6RingSize])
		}
		p.mu.Unlock()
		_ = json.NewEncoder(w).Encode(out)
	case "wait":
		key := q.Get("kind")
		if s, n := q.Get("stream"), q.Get("name"); s != "" && n != "" {
			key += "|s=" + s + "|n=" + n
		} else if s != "" {
			key += "|s=" + s
		} else if n != "" {
			key += "|n=" + n
		}
		count, _ := strconv.ParseInt(q.Get("count"), 10, 64)
		count = max(count, 1)
		timeout, _ := strconv.Atoi(q.Get("timeout_ms"))
		if timeout <= 0 {
			timeout = 5000
		}
		timer := time.NewTimer(time.Duration(timeout) * time.Millisecond)
		defer timer.Stop()
		for {
			p.mu.Lock()
			c, ch := p.waits[key], p.changed
			if c != nil && c.Count >= count {
				data, _ := json.Marshal(c)
				p.mu.Unlock()
				_, _ = w.Write(data)
				return true
			}
			p.mu.Unlock()
			select {
			case <-ch:
			case <-timer.C:
				w.WriteHeader(http.StatusRequestTimeout)
				_ = json.NewEncoder(w).Encode(map[string]any{"key": key, "count": 0})
				return true
			case <-r.Context().Done():
				return true
			}
		}
	case "release":
		if r.Method != http.MethodPost || q.Get("gate") == "" {
			http.Error(w, "POST ?gate=NAME[&n=1]", http.StatusBadRequest)
			return true
		}
		n, _ := strconv.ParseInt(q.Get("n"), 10, 64)
		p.releaseGate(q.Get("gate"), max(n, 1))
		_, _ = w.Write([]byte("{}\n"))
	default:
		http.NotFound(w, r)
	}
	return true
}

// Conditional HTTP routes.

func (p *phase6) route(r *http.Request) *p6Route {
	for _, route := range p.cfg.HTTP {
		if route.Method == r.Method && route.Path == r.URL.Path {
			return route
		}
	}
	return nil
}

func (p *phase6) serveRoute(f *fixture, w http.ResponseWriter, r *http.Request, index int) bool {
	route := p.route(r)
	if route == nil {
		return false
	}
	var chosen *p6Response
	p.mu.Lock()
	for _, resp := range route.Responses {
		if resp.Times > 0 && resp.used >= resp.Times {
			continue
		}
		matched := true
		for k, v := range resp.Require {
			if r.Header.Get(k) != v {
				matched = false
				break
			}
		}
		if matched {
			resp.used++
			chosen = resp
			break
		}
	}
	p.mu.Unlock()
	code := http.StatusInternalServerError
	if chosen != nil {
		code = max(chosen.Status, 0)
		if code == 0 {
			code = http.StatusOK
		}
	}
	p.emit(monotonicNS(), "http_response", p6Fields{"method": r.Method, "path": r.URL.Path, "status": code, "matched": chosen != nil})
	if chosen == nil {
		http.Error(w, "fixture: no matching phase6 response", code)
		return true
	}
	if chosen.Gate != "" {
		p.emit(monotonicNS(), "gate_wait", p6Fields{"name": chosen.Gate, "path": r.URL.Path})
		if !p.waitGate(r.Context(), chosen.Gate) {
			return true
		}
	}
	if !pause(r.Context(), chosen.DelayMS) {
		return true
	}
	w.Header().Set("Content-Type", "application/json")
	for k, v := range chosen.Headers {
		w.Header().Set(k, v)
	}
	body := strings.ReplaceAll(chosen.Body, "{{grpc_port}}", strconv.Itoa(f.grpcPort))
	p.mu.Lock()
	for name, port := range p.ports {
		body = strings.ReplaceAll(body, "{{port:"+name+"}}", strconv.Itoa(port))
	}
	p.mu.Unlock()
	w.WriteHeader(code)
	f.mark(index, func(m *milestone) { m.HeadersMonotonicNS = monotonicNS(); m.UncompressedBodyBytes = len(body) })
	f.mark(index, func(m *milestone) { m.FinalWriteStartedMonotonicNS = monotonicNS() })
	if _, err := io.WriteString(w, body); err == nil {
		f.mark(index, func(m *milestone) {
			m.FirstBodyMonotonicNS = monotonicNS()
			m.BodyBytesWritten = len(body)
		})
	}
	f.mark(index, func(m *milestone) { m.FinalBodyMonotonicNS = monotonicNS() })
	return true
}

// Flight DoGet.

type (
	p6RPCKey  struct{}
	p6ConnKey struct{}
)

type p6RPC struct{ wire atomic.Int64 }

type p6Stats struct{ p *phase6 }

func (s *p6Stats) TagRPC(ctx context.Context, _ *stats.RPCTagInfo) context.Context {
	return context.WithValue(ctx, p6RPCKey{}, &p6RPC{})
}

func (s *p6Stats) HandleRPC(ctx context.Context, st stats.RPCStats) {
	if out, ok := st.(*stats.OutPayload); ok {
		if rpc, ok := ctx.Value(p6RPCKey{}).(*p6RPC); ok {
			rpc.wire.Add(int64(out.WireLength))
		}
	}
}

func (s *p6Stats) TagConn(ctx context.Context, info *stats.ConnTagInfo) context.Context {
	return context.WithValue(ctx, p6ConnKey{}, info.RemoteAddr.String())
}

func (s *p6Stats) HandleConn(ctx context.Context, st stats.ConnStats) {
	remote, _ := ctx.Value(p6ConnKey{}).(string)
	switch st.(type) {
	case *stats.ConnBegin:
		s.p.emit(monotonicNS(), "conn_begin", p6Fields{"remote": remote})
	case *stats.ConnEnd:
		s.p.emit(monotonicNS(), "conn_end", p6Fields{"remote": remote})
	}
}

// doGet handles phase-six streams. handled=false defers an unknown stream to the
// legacy flight plan when one is configured.
func (p *phase6) doGet(f *fixture, ticket *flight.Ticket, stream flight.FlightService_DoGetServer) (handled bool, err error) {
	// A fixture bug must surface as a distinct status, not kill the process.
	defer func() {
		if v := recover(); v != nil {
			p.emit(monotonicNS(), "fixture_panic", p6Fields{"panic": fmt.Sprint(v)})
			p.writeSummary()
			handled, err = true, status.Errorf(codes.Internal, "fixture panic: %v", v)
		}
	}()
	ctx := stream.Context()
	received := monotonicNS()
	var parsed struct {
		Stream *string `json:"stream"`
	}
	name := ""
	if json.Unmarshal(ticket.Ticket, &parsed) == nil && parsed.Stream != nil {
		name = *parsed.Stream
	}
	spec := p.cfg.Streams[name]
	if spec == nil && f.plan.Flight != nil {
		return false, nil
	}
	md, _ := metadata.FromIncomingContext(ctx)
	contract := map[string]string{}
	for _, h := range contractHeaders {
		if values := md.Get(h); len(values) > 0 {
			contract[h] = strings.Join(values, ",")
		}
	}
	f.record(request{
		Protocol: "flight", Method: "DoGet", Path: "/arrow.flight.protocol.FlightService/DoGet", Headers: contract,
		Body: string(ticket.Ticket), TimeUnixNS: time.Now().UnixNano(), Unexpected: spec == nil,
	})
	authorized := f.authorized(contract)
	if p.cfg.FlightAuth != nil {
		authorized = true
		for k, v := range p.cfg.FlightAuth {
			if values := md.Get(k); len(values) != 1 || values[0] != v {
				authorized = false
			}
		}
	}
	all := map[string][]string{}
	for k, v := range md {
		all[k] = v
	}
	var tlsInfo any
	remote := ""
	if pr, ok := peer.FromContext(ctx); ok {
		remote = pr.Addr.String()
		if info, ok := pr.AuthInfo.(credentials.TLSInfo); ok {
			tlsInfo = map[string]any{
				"server_name": info.State.ServerName, "alpn": info.State.NegotiatedProtocol,
				"version": tls.VersionName(info.State.Version),
			}
		}
	}
	expected := []byte(nil)
	if spec != nil && spec.ExpectedTicket != nil {
		expected = []byte(*spec.ExpectedTicket)
	} else {
		expected, _ = json.Marshal(map[string]string{"stream": name})
	}
	key := name
	if spec == nil {
		key = "<unknown>" // bounded: arbitrary client stream names share one bucket
	}
	p.mu.Lock()
	p.dogets++
	doget := p.dogets - 1
	stats := p.stats(key)
	stats.DoGets++
	if stats.LastTerminal != nil {
		stats.ReconnectGapNS.add(received - stats.LastTerminal.TNS)
	}
	if stats.FirstDoGetNS == nil {
		stats.FirstDoGetNS = &received
		if spec != nil {
			width := int64(spec.SummaryBucketMS)
			if width == 0 {
				width = 60000
			}
			stats.Buckets = &p6Buckets{OriginNS: received, BucketMS: width, Counts: []p6Bucket{}}
		}
	}
	last := received
	stats.LastDoGetNS = &last
	v := &stats.RequestValidation
	v.AuthChecks++
	v.TicketChecks++
	if !authorized {
		v.InvalidAuth++
	}
	if !bytes.Equal(ticket.Ticket, expected) {
		v.TicketMismatches++
	}
	if spec == nil {
		v.UnexpectedRequests++
	}
	if p.aboutCompleted == nil || *p.aboutCompleted > received {
		v.DoGetBeforeAbout++
	}
	// Only accepted DoGets advance the script, so a rejected attempt does not
	// consume a subscription.
	sub := stats.Subscriptions
	authority := ""
	if values := md.Get(":authority"); len(values) > 0 {
		authority = values[0]
	}
	if _, seen := stats.Authorities[authority]; seen || len(stats.Authorities) < 16 {
		stats.Authorities[authority]++
	} else {
		stats.Authorities["<other>"]++
	}
	p.mu.Unlock()
	p.emit(received, "doget", p6Fields{
		"ticket_matches": bytes.Equal(ticket.Ticket, expected), "stream": name, "sub": sub, "doget": doget, "ticket": string(ticket.Ticket),
		"ticket_b64": base64.StdEncoding.EncodeToString(ticket.Ticket), "metadata": all, "authorized": authorized,
		"unexpected": spec == nil, "tls": tlsInfo, "peer": remote, "authority": authority,
	})
	rpc, _ := ctx.Value(p6RPCKey{}).(*p6RPC)
	run := &p6Run{p: p, name: key, sub: sub, doget: doget, stream: stream, ctx: ctx, rpc: rpc, spec: spec, remote: remote}
	if !authorized {
		return true, run.finish(status.Error(codes.Unauthenticated, "fixture: incorrect authentication"), "unauthenticated")
	}
	if spec == nil {
		return true, run.finish(status.Error(codes.NotFound, "fixture: unknown stream"), "unknown_stream")
	}
	ops := spec.ops(sub)
	if ops == nil {
		return true, run.finish(status.Error(codes.InvalidArgument, "fixture subscriptions exhausted"), "exhausted")
	}
	p.mu.Lock()
	stats.Subscriptions++
	p.mu.Unlock()
	run.name = name
	return true, run.execute(ops)
}

// p6Capture collects IPC payloads as FlightData so a run controls sending.
type p6Message struct {
	fd  *flight.FlightData
	typ ipc.MessageType
}

type p6Capture struct{ pending []p6Message }

func (c *p6Capture) Start() error { return nil }
func (c *p6Capture) Close() error { return nil }
func (c *p6Capture) WritePayload(payload ipc.Payload) error {
	meta := payload.Meta()
	header := append([]byte(nil), meta.Bytes()...)
	meta.Release()
	var body bytes.Buffer
	if err := payload.SerializeBody(&body); err != nil {
		return err
	}
	msg := ipc.NewMessage(memory.NewBufferBytes(header), memory.NewBufferBytes(body.Bytes()))
	typ := msg.Type()
	msg.Release()
	c.pending = append(c.pending, p6Message{fd: &flight.FlightData{DataHeader: header, DataBody: body.Bytes()}, typ: typ})
	return nil
}

func (c *p6Capture) take() []p6Message {
	out := c.pending
	c.pending = nil
	return out
}

type p6Run struct {
	p          *phase6
	spec       *p6Stream
	name       string
	sub        int64
	doget      int64
	stream     flight.FlightService_DoGetServer
	ctx        context.Context
	rpc        *p6RPC
	writer     *ipc.Writer
	capture    *p6Capture
	schemaSent bool
	subBatch   int64
	dict       *p6DictState
	dictChange string // initial|delta|replacement for the batch being built
	dictReset  bool
	dictSeen   bool
	wireSeen   int64
	remote     string
}

func (r *p6Run) fields(extra p6Fields) p6Fields {
	extra["stream"], extra["sub"], extra["doget"] = r.name, r.sub, r.doget
	return extra
}

func (r *p6Run) writerOptions(schema *arrow.Schema) []ipc.Option {
	opts := []ipc.Option{ipc.WithSchema(schema), ipc.WithAllocator(memory.DefaultAllocator)}
	switch r.spec.Compression {
	case "lz4":
		opts = append(opts, ipc.WithLZ4())
	case "zstd":
		opts = append(opts, ipc.WithZstd())
	}
	if r.spec.Dictionary != nil && (r.spec.Dictionary.Mode == "delta" || r.spec.Dictionary.Mode == "cycle") {
		opts = append(opts, ipc.WithDictionaryDeltas(true))
	}
	return opts
}

func (r *p6Run) ensureWriter() {
	if r.writer == nil {
		r.capture = &p6Capture{}
		r.writer = ipc.NewWriterWithPayloadWriter(r.capture, r.writerOptions(p6Schema(r.spec.Schema))...)
	}
}

// encodeWith encodes rec (or only the schema when rec is nil) with a throwaway
// writer, used for explicit/foreign schema and corruption probes.
func (r *p6Run) encodeWith(schema *arrow.Schema, rec arrow.Record) ([]p6Message, error) {
	capture := &p6Capture{}
	w := ipc.NewWriterWithPayloadWriter(capture, r.writerOptions(schema)...)
	if rec != nil {
		if err := w.Write(rec); err != nil {
			return nil, err
		}
	}
	if err := w.Close(); err != nil {
		return nil, err
	}
	return capture.take(), nil
}

func (r *p6Run) send(fd *flight.FlightData) (int, error) {
	return proto.Size(fd), r.stream.Send(fd)
}

func (r *p6Run) sendMessage(op string, fd *flight.FlightData) error {
	n, err := r.send(fd)
	r.p.emit(monotonicNS(), "message_sent", r.fields(p6Fields{"op": op, "bytes": n, "error": errString(err)}))
	if err == nil {
		r.countMessage(n)
	}
	return err
}

func (r *p6Run) countMessage(n int) {
	r.p.mu.Lock()
	defer r.p.mu.Unlock()
	s := r.p.stats(r.name)
	s.Messages++
	s.EncodedBytes += int64(n)
	s.MaxMessageBytes = max(s.MaxMessageBytes, int64(n))
}

func errString(err error) any {
	if err == nil {
		return nil
	}
	return err.Error()
}

func (r *p6Run) sendSchema(kind string, explicit bool) error {
	msgs, err := r.encodeWith(p6Schema(kind), nil)
	if err != nil {
		return err
	}
	for _, m := range msgs {
		n, err := r.send(m.fd)
		r.p.emit(monotonicNS(), "schema_sent", r.fields(p6Fields{"schema": kind, "bytes": n, "explicit": explicit, "error": errString(err)}))
		if err != nil {
			return err
		}
		r.countMessage(n)
	}
	if kind == r.spec.Schema {
		r.schemaSent = true
	}
	return nil
}

func (r *p6Run) execute(ops []p6Op) error {
	for i := range ops {
		op := &ops[i]
		var err error
		switch op.Op {
		case "schema":
			kind := op.Schema
			if kind == "" {
				kind = r.spec.Schema
			}
			err = r.sendSchema(kind, true)
		case "batch":
			for k := 0; k < max(op.Count, 1) && err == nil; k++ {
				err = r.batch(op.Rows, r.payloadBytes(op), op.TargetMessageBytes, nil)
			}
		case "live":
			err = r.live(op)
		case "sleep":
			r.p.emit(monotonicNS(), "sleep", r.fields(p6Fields{"ms": op.MS}))
			if !pause(r.ctx, op.MS) {
				return r.finish(r.ctx.Err(), "client_cancel")
			}
		case "gate":
			r.p.emit(monotonicNS(), "gate_wait", r.fields(p6Fields{"name": op.Name}))
			if !r.p.waitGate(r.ctx, op.Name) {
				return r.finish(r.ctx.Err(), "client_cancel")
			}
			r.p.emit(monotonicNS(), "gate_pass", r.fields(p6Fields{"name": op.Name}))
		case "hold":
			r.p.emit(monotonicNS(), "hold", r.fields(p6Fields{}))
			<-r.ctx.Done()
			return r.finish(r.ctx.Err(), "client_cancel")
		case "headers":
			err = r.stream.SendHeader(metadata.MD{})
			r.p.emit(monotonicNS(), "headers_sent", r.fields(p6Fields{"error": errString(err)}))
		case "status":
			message := op.Message
			if op.MessageBytes > len(message) {
				message += strings.Repeat("x", op.MessageBytes-len(message))
			}
			if op.TrailerPadBytes > 0 {
				r.stream.SetTrailer(metadata.Pairs("x-fixture-pad", strings.Repeat("x", op.TrailerPadBytes)))
			}
			if op.MessageBytes > 0 || op.TrailerPadBytes > 0 {
				r.p.emit(monotonicNS(), "trailer_sized", r.fields(p6Fields{"grpc_message_bytes": len(message), "trailer_pad_bytes": op.TrailerPadBytes}))
			}
			if op.Code == 0 {
				return r.finish(nil, "status")
			}
			return r.finish(status.Error(codes.Code(op.Code), message), "status")
		case "conn_close", "conn_reset", "goaway":
			return r.connAction(op)
		case "eof":
			return r.finish(nil, "eof")
		case "metadata_only":
			for k := 0; k < max(op.Count, 1) && err == nil; k++ {
				err = r.sendMessage("metadata_only", &flight.FlightData{AppMetadata: []byte(op.AppMetadata)})
			}
		case "raw":
			header, _ := base64.StdEncoding.DecodeString(op.HeaderB64)
			body, _ := base64.StdEncoding.DecodeString(op.BodyB64)
			appMeta, _ := base64.StdEncoding.DecodeString(op.AppMetadataB64)
			err = r.sendMessage("raw", &flight.FlightData{DataHeader: header, DataBody: body, AppMetadata: appMeta})
		case "corrupt":
			err = r.corrupt(op)
		}
		if err != nil {
			if r.ctx.Err() != nil {
				return r.finish(r.ctx.Err(), "client_cancel")
			}
			if _, ok := status.FromError(err); ok && status.Code(err) != codes.Unknown {
				return r.finish(err, "send_error")
			}
			return r.finish(status.Error(codes.Internal, "fixture: "+err.Error()), "fixture_error")
		}
	}
	return r.finish(status.Error(codes.InvalidArgument, "fixture stream complete"), "complete")
}

func (r *p6Run) payloadBytes(op *p6Op) int {
	if op.PayloadBytes != nil {
		return *op.PayloadBytes
	}
	return r.spec.PayloadBytes
}

// finish records the terminal outcome and publishes the summary before the
// status reaches the client.
func (r *p6Run) finish(err error, reason string) error {
	if r.writer != nil {
		_ = r.writer.Close()
		r.writer = nil
	}
	if r.dict != nil {
		r.dict.release()
		r.dict = nil
	}
	code, message := "OK", ""
	if err != nil {
		st := status.Convert(err)
		code, message = st.Code().String(), st.Message()
		if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			code = codes.Canceled.String()
		}
	}
	t := monotonicNS()
	r.p.mu.Lock()
	s := r.p.stats(r.name)
	s.Terminals[code]++
	s.LastTerminal = &p6Terminal{Code: code, Message: message, Reason: reason, Sub: r.sub, TNS: t}
	if r.rpc != nil {
		wire := r.rpc.wire.Load()
		s.GRPCWireBytes += wire - r.wireSeen
		r.wireSeen = wire
	}
	r.p.mu.Unlock()
	r.p.emit(t, "terminal", r.fields(p6Fields{"code": code, "message": message, "reason": reason}))
	r.p.writeSummary()
	return err
}

func (r *p6Run) live(op *p6Op) error {
	interval := time.Duration(op.IntervalMS) * time.Millisecond
	duration := time.Duration(op.DurationMS) * time.Millisecond
	start := time.Now()
	startNS := monotonicNS()
	r.beginLive(op)
	var emitted int64
	reason := "count"
	var err error
loop:
	for k := 0; op.Batches <= 0 || k < op.Batches; k++ {
		due := start.Add(time.Duration(k) * interval)
		if duration > 0 && (due.Sub(start) >= duration || time.Since(start) >= duration) {
			reason = "deadline"
			break
		}
		if wait := time.Until(due); wait > 0 {
			t := time.NewTimer(wait)
			select {
			case <-r.ctx.Done():
				t.Stop()
				err, reason = r.ctx.Err(), "termination"
				break loop
			case <-t.C:
			}
		}
		lateness := time.Since(due).Nanoseconds()
		if err = r.batch(op.RowsPerBatch, r.payloadBytes(op), 0, &p6BatchSchedule{dueNS: startNS + int64(k)*int64(interval), latenessNS: lateness}); err != nil {
			reason = "termination"
			break
		}
		emitted++
	}
	r.recordLive(op, emitted, reason, startNS)
	return err
}

// liveSchedule is the deterministic batch count of a live op, or nil for an
// unbounded burst (duration with no interval).
func liveSchedule(op *p6Op) *int64 {
	var n int64
	switch {
	case op.DurationMS > 0 && op.IntervalMS <= 0:
		return nil
	case op.DurationMS > 0:
		n = (int64(op.DurationMS) + int64(op.IntervalMS) - 1) / int64(op.IntervalMS)
		if op.Batches > 0 {
			n = min(n, int64(op.Batches))
		}
	default:
		n = int64(op.Batches)
	}
	return &n
}

// Count schedules when an op starts, including an op blocked inside Send.
func (r *p6Run) beginLive(op *p6Op) {
	r.p.mu.Lock()
	defer r.p.mu.Unlock()
	l := r.p.stats(r.name).live()
	l.Ops++
	if n := liveSchedule(op); n == nil {
		l.UnboundedOps++
		l.ScheduledBatches = nil
	} else if l.ScheduledBatches != nil {
		*l.ScheduledBatches += *n
	}
}

func (r *p6Run) recordLive(op *p6Op, emitted int64, reason string, startNS int64) {
	scheduled := liveSchedule(op)
	ended := monotonicNS()
	var deadline *int64
	if op.DurationMS > 0 {
		d := startNS + int64(op.DurationMS)*int64(time.Millisecond)
		deadline = &d
	}
	r.p.mu.Lock()
	defer r.p.mu.Unlock()
	s := r.p.stats(r.name)
	l := s.live()
	switch reason {
	case "count":
		l.StoppedByCount++
	case "deadline":
		l.StoppedByDeadline++
	default:
		l.StoppedByTermination++
	}
	if scheduled != nil {
		missing := max(*scheduled-emitted, 0)
		switch reason {
		case "deadline":
			l.OmittedByDeadline += missing
		case "termination":
			l.OmittedByTermination += missing
		}
	}
	l.Last = &p6LiveLast{
		Scheduled: scheduled, Emitted: emitted, StopReason: reason, StartedNS: startNS, DeadlineNS: deadline,
		EndedNS: ended, IntervalMS: op.IntervalMS, DurationMS: op.DurationMS, Batches: op.Batches,
	}
}

// p6Agg is an exact count/sum/max aggregate of nanosecond values.
type p6Agg struct {
	Count int64 `json:"count"`
	Sum   int64 `json:"sum"`
	Max   int64 `json:"max"`
}

func (a *p6Agg) add(ns int64) {
	a.Count++
	a.Sum += ns
	a.Max = max(a.Max, ns)
}

type p6LiveLast struct {
	Scheduled  *int64 `json:"scheduled"`
	Emitted    int64  `json:"emitted"`
	StopReason string `json:"stop_reason"`
	StartedNS  int64  `json:"started_ns"`
	DeadlineNS *int64 `json:"deadline_ns"`
	EndedNS    int64  `json:"ended_ns"`
	IntervalMS int    `json:"interval_ms"`
	DurationMS int    `json:"duration_ms"`
	Batches    int    `json:"batches"`
}

// p6Live aggregates every live op of a stream. lateness_ns is due time to the
// first batch Send; send_call_ns is wall time inside grpc-go Send
// calls for the batch (marshal/copy plus any flow-control wait), not pure
// network blocking.
type p6Live struct {
	Ops                  int64       `json:"ops"`
	StoppedByCount       int64       `json:"stopped_by_count"`
	StoppedByDeadline    int64       `json:"stopped_by_deadline"`
	StoppedByTermination int64       `json:"stopped_by_termination"`
	ScheduledBatches     *int64      `json:"scheduled_batches"`
	UnboundedOps         int64       `json:"unbounded_ops"`
	EmittedBatches       int64       `json:"emitted_batches"`
	OmittedByDeadline    int64       `json:"omitted_by_deadline"`
	OmittedByTermination int64       `json:"omitted_by_termination"`
	LatenessNS           p6Agg       `json:"lateness_ns"`
	SendCallNS           p6Agg       `json:"send_call_ns"`
	Last                 *p6LiveLast `json:"last"`
}

func (s *p6StreamStats) live() *p6Live {
	if s.Live == nil {
		zero := int64(0)
		s.Live = &p6Live{ScheduledBatches: &zero}
	}
	return s.Live
}

// p6DictCounts classifies DictionaryBatch messages by the change the fixture
// made. Builder-made dictionaries start with initial, then replacements.
type p6DictCounts struct {
	Messages     int64 `json:"messages"`
	Initial      int64 `json:"initial"`
	Deltas       int64 `json:"deltas"`
	Replacements int64 `json:"replacements"`
	Resets       int64 `json:"resets"`
	MaxSize      int64 `json:"max_size"`
}

type p6Bucket struct {
	Batches                int64 `json:"batches"`
	Rows                   int64 `json:"rows"`
	DictionaryMessages     int64 `json:"dictionary_messages"`
	DictionaryDeltas       int64 `json:"dictionary_deltas"`
	DictionaryReplacements int64 `json:"dictionary_replacements"`
}

// p6Buckets counts batches by batch_emit_start time relative to the stream's
// first DoGet; at most 512 buckets, later batches go to overflow.
type p6Buckets struct {
	OriginNS int64      `json:"origin_ns"`
	BucketMS int64      `json:"bucket_ms"`
	Overflow int64      `json:"overflow"`
	Counts   []p6Bucket `json:"counts"`
}

const p6MaxBuckets = 512

// batch builds, encodes and sends one record batch on the run's IPC writer.
type p6BatchSchedule struct {
	dueNS      int64
	latenessNS int64 // retained batch event semantics: before batch preparation
}

func (r *p6Run) batch(rows, payload, target int, schedule *p6BatchSchedule) error {
	r.ensureWriter()
	if target > 0 && r.spec.Schema == "wide" && payload == 0 {
		payload = max(target-4096, 0) / max(rows, 1)
	}
	r.p.mu.Lock()
	s := r.p.stats(r.name)
	seqStart, ordinal := s.NextSequence, s.Batches
	r.p.mu.Unlock()
	r.dictChange, r.dictReset = "", false
	rec := r.record(r.spec.Schema, seqStart, rows, payload)
	defer rec.Release()
	if err := r.writer.Write(rec); err != nil {
		return err
	}
	msgs := r.capture.take()
	if len(msgs) > 0 && msgs[0].typ == ipc.MessageSchema {
		if r.schemaSent {
			msgs = msgs[1:]
		} else {
			n, err := r.send(msgs[0].fd)
			r.p.emit(monotonicNS(), "schema_sent", r.fields(p6Fields{"schema": r.spec.Schema, "bytes": n, "explicit": false, "error": errString(err)}))
			if err != nil {
				return err
			}
			r.countMessage(n)
			r.schemaSent = true
			msgs = msgs[1:]
		}
	}
	if target > 0 && len(msgs) > 0 {
		padToSize(msgs[len(msgs)-1].fd, target)
	}
	var expected []byte
	var expectedNS int64
	if r.p.cfg.ExpectedOutput {
		t0 := monotonicNS()
		var buf bytes.Buffer
		_ = array.RecordToJSON(rec, &buf) // Go tail ignores this error too.
		buf.WriteByte('\n')
		expected = buf.Bytes()
		expectedNS = monotonicNS() - t0
	}
	sizes := make([]int, len(msgs))
	encoded, maxMessage, dictMessages := 0, 0, 0
	for i, m := range msgs {
		sizes[i] = proto.Size(m.fd)
		encoded += sizes[i]
		maxMessage = max(maxMessage, sizes[i])
		if m.typ == ipc.MessageDictionaryBatch {
			dictMessages++
		}
	}
	decoded := recordBytes(rec)
	common := p6Fields{
		"batch": ordinal, "sub_batch": r.subBatch, "rows": rows, "seq_start": seqStart, "messages": len(msgs),
		"dictionary_messages": dictMessages, "encoded_bytes": encoded, "max_message_bytes": maxMessage, "decoded_bytes": decoded,
	}
	if schedule != nil {
		common["lateness_ns"] = schedule.latenessNS
	}
	start := monotonicNS()
	if rows > 0 {
		r.p.mu.Lock()
		if s.FirstNonemptyBatchNS == nil {
			first := start
			s.FirstNonemptyBatchNS = &first
		}
		r.p.mu.Unlock()
	}
	r.p.mu.Lock()
	if r.bucket(start) == nil && s.Buckets != nil {
		s.Buckets.Overflow++
	}
	r.p.mu.Unlock()
	r.p.emit(start, "batch_emit_start", r.fields(cloneFields(common)))
	sendStart := monotonicNS()
	var sendErr error
	for _, m := range msgs {
		if sendErr = r.stream.Send(m.fd); sendErr != nil {
			break
		}
		if m.typ == ipc.MessageDictionaryBatch {
			r.countDictionary(start)
		}
	}
	done := monotonicNS()
	r.p.mu.Lock()
	s.BatchSendCallNS.add(done - sendStart)
	if schedule != nil {
		s.live().SendCallNS.add(done - sendStart)
	}
	r.p.mu.Unlock()
	if sendErr != nil {
		r.p.emit(done, "batch_emit_failed", r.fields(p6Fields{"batch": ordinal, "error": sendErr.Error()}))
		return sendErr
	}
	r.subBatch++
	r.p.mu.Lock()
	s.Batches++
	if rows == 0 {
		s.ZeroRowBatches++
	}
	s.Rows += int64(rows)
	s.NextSequence += int64(rows)
	s.Messages += int64(len(msgs))
	s.EncodedBytes += int64(encoded)
	s.DecodedBytes += decoded
	s.MaxMessageBytes = max(s.MaxMessageBytes, int64(maxMessage))
	if s.hash != nil {
		s.hash.Write(expected)
		s.stdoutBytes += int64(len(expected))
		s.renderedLines += int64(bytes.Count(expected, []byte{'\n'}) - 1)
		s.ExpectedRows += int64(rows)
		s.ExpectedOutputNS += expectedNS
	}
	if schedule != nil {
		l := s.live()
		l.EmittedBatches++
		l.LatenessNS.add(max(sendStart-schedule.dueNS, 0))
	}
	if c := r.bucket(start); c != nil {
		c.Batches++
		c.Rows += int64(rows)
	}
	r.p.mu.Unlock()
	end := cloneFields(common)
	end["send_ns"] = done - start
	r.p.emit(done, "batch_emit_done", r.fields(end))
	return nil
}

// bucket must be called with p.mu held. Overflow counts batch starts beyond
// the retained time range, regardless of how many dictionaries the batch has.
func (r *p6Run) bucket(start int64) *p6Bucket {
	s := r.p.stats(r.name)
	if s.FirstDoGetNS == nil {
		return nil
	}
	b := s.Buckets
	if b == nil {
		width := int64(r.spec.SummaryBucketMS)
		if width <= 0 {
			width = 60000
		}
		b = &p6Buckets{OriginNS: *s.FirstDoGetNS, BucketMS: width, Counts: []p6Bucket{}}
		s.Buckets = b
	}
	i := (start - b.OriginNS) / (b.BucketMS * int64(time.Millisecond))
	if start < b.OriginNS || i >= p6MaxBuckets {
		return nil
	}
	for int64(len(b.Counts)) <= i {
		b.Counts = append(b.Counts, p6Bucket{})
	}
	return &b.Counts[i]
}

// Dictionary sends are exact even when a later record Send fails.
func (r *p6Run) countDictionary(start int64) {
	r.p.mu.Lock()
	defer r.p.mu.Unlock()
	s := r.p.stats(r.name)
	if s.DictionaryCounts == nil {
		s.DictionaryCounts = &p6DictCounts{}
	}
	d := s.DictionaryCounts
	change := r.dictChange
	if !r.dictSeen {
		change = "initial"
	} else if change == "" || change == "initial" {
		change = "replacement"
	}
	d.Messages++
	c := r.bucket(start)
	if c != nil {
		c.DictionaryMessages++
	}
	switch change {
	case "initial":
		d.Initial++
	case "delta":
		d.Deltas++
		if c != nil {
			c.DictionaryDeltas++
		}
	case "replacement":
		d.Replacements++
		if c != nil {
			c.DictionaryReplacements++
		}
	}
	if r.dictReset {
		d.Resets++
	}
	if r.dict != nil {
		d.MaxSize = max(d.MaxSize, int64(r.dict.size))
	}
	r.dictSeen = true
}

func cloneFields(in p6Fields) p6Fields {
	out := make(p6Fields, len(in)+4)
	for k, v := range in {
		out[k] = v
	}
	return out
}

// padToSize pads app_metadata so the FlightData protobuf is exactly target
// bytes when reachable; the actual size is reported by the caller.
func padToSize(fd *flight.FlightData, target int) {
	need := target - proto.Size(fd)
	if need < 2 {
		return
	}
	for l := need - 2; l >= 0; l-- {
		fd.AppMetadata = make([]byte, l)
		if proto.Size(fd) == target {
			return
		}
		if proto.Size(fd) < target {
			break
		}
	}
	fd.AppMetadata = nil
}

func recordBytes(rec arrow.Record) int64 {
	total := int64(0)
	for _, col := range rec.Columns() {
		total += dataBytes(col.Data())
	}
	return total
}

func dataBytes(d arrow.ArrayData) int64 {
	// Arrow returns typed-nil *array.Data for absent dictionaries.
	if ad, ok := d.(*array.Data); d == nil || (ok && ad == nil) {
		return 0
	}
	total := int64(0)
	for _, b := range d.Buffers() {
		if b != nil {
			total += int64(b.Len())
		}
	}
	for _, c := range d.Children() {
		total += dataBytes(c)
	}
	if d.DataType().ID() == arrow.DICTIONARY {
		total += dataBytes(d.Dictionary())
	}
	return total
}

func (r *p6Run) corrupt(op *p6Op) error {
	switch op.Kind {
	case "garbage_header":
		return r.sendMessage(op.Kind, &flight.FlightData{DataHeader: []byte{0xde, 0xad, 0xbe, 0xef, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08}})
	case "empty_message":
		return r.sendMessage(op.Kind, &flight.FlightData{})
	case "truncated_body":
		r.ensureWriter()
		r.p.mu.Lock()
		seq := r.p.stats(r.name).NextSequence
		r.p.mu.Unlock()
		rec := r.record(r.spec.Schema, seq, max(op.Rows, 1), r.payloadBytes(op))
		defer rec.Release()
		if err := r.writer.Write(rec); err != nil {
			return err
		}
		msgs := r.capture.take()
		for _, m := range msgs {
			if m.typ == ipc.MessageSchema && r.schemaSent {
				continue
			}
			if m.typ == ipc.MessageSchema {
				r.schemaSent = true
			}
			if m.typ == ipc.MessageRecordBatch {
				m.fd.DataBody = m.fd.DataBody[:len(m.fd.DataBody)/2]
			}
			if err := r.sendMessage(op.Kind+":"+m.typ.String(), m.fd); err != nil {
				return err
			}
		}
		return nil
	case "foreign_batch":
		kind := op.Schema
		if kind == "" {
			kind = "dup"
		}
		return r.sendForeign(op.Kind, kind, max(op.Rows, 1), func(t ipc.MessageType) bool { return t == ipc.MessageRecordBatch })
	case "dictionary_before_schema", "dictionary_only":
		return r.sendForeign(op.Kind, "dictionary", max(op.Rows, 1), func(t ipc.MessageType) bool { return t == ipc.MessageDictionaryBatch })
	case "missing_dictionary":
		// Schema (unless already sent) and a record batch whose dictionary was never sent.
		return r.sendForeign(op.Kind, "dictionary", max(op.Rows, 1), func(t ipc.MessageType) bool {
			if t == ipc.MessageSchema {
				if r.schemaSent {
					return false
				}
				r.schemaSent = r.spec.Schema == "dictionary"
				return true
			}
			return t == ipc.MessageRecordBatch
		})
	}
	return fmt.Errorf("unknown corrupt kind %q", op.Kind)
}

func (r *p6Run) sendForeign(op, kind string, rows int, keep func(ipc.MessageType) bool) error {
	saved := r.dict
	r.dict = nil
	rec := r.record(kind, 0, rows, 0)
	msgs, err := r.encodeWith(p6Schema(kind), rec)
	rec.Release()
	if r.dict != nil {
		r.dict.release()
	}
	r.dict = saved
	if err != nil {
		return err
	}
	for _, m := range msgs {
		if keep(m.typ) {
			if err := r.sendMessage(op+":"+m.typ.String(), m.fd); err != nil {
				return err
			}
		}
	}
	return nil
}

// Schemas and deterministic data.

var p6BasicFields = []arrow.Field{
	{Name: "sequence", Type: arrow.PrimitiveTypes.Int64},
	{Name: "large_integer", Type: arrow.PrimitiveTypes.Int64},
	{Name: "value", Type: arrow.PrimitiveTypes.Float64},
	{Name: "message", Type: arrow.BinaryTypes.String},
	{Name: "optional", Type: arrow.BinaryTypes.String, Nullable: true},
	{Name: "p_timestamp", Type: &arrow.TimestampType{Unit: arrow.Microsecond}},
}

var p6DictType = &arrow.DictionaryType{IndexType: arrow.PrimitiveTypes.Int32, ValueType: arrow.BinaryTypes.String}

var p6Schemas sync.Map

func p6Schema(kind string) *arrow.Schema {
	if s, ok := p6Schemas.Load(kind); ok {
		return s.(*arrow.Schema)
	}
	var fields []arrow.Field
	switch kind {
	case "basic":
		fields = p6BasicFields
	case "wide":
		fields = []arrow.Field{{Name: "sequence", Type: arrow.PrimitiveTypes.Int64}, {Name: "payload", Type: arrow.BinaryTypes.String}}
	case "values":
		for _, c := range p6ValueColumns() {
			fields = append(fields, c.field)
		}
	case "dup":
		fields = []arrow.Field{
			{Name: "zeta", Type: arrow.PrimitiveTypes.Int64},
			{Name: "alpha", Type: arrow.BinaryTypes.String},
			{Name: "zeta", Type: arrow.BinaryTypes.String},
			{Name: "mid", Type: arrow.PrimitiveTypes.Float64},
			{Name: "sequence", Type: arrow.PrimitiveTypes.Int64},
		}
	case "nan":
		fields = []arrow.Field{{Name: "sequence", Type: arrow.PrimitiveTypes.Int64}, {Name: "value", Type: arrow.PrimitiveTypes.Float64}}
	case "dictionary":
		fields = []arrow.Field{{Name: "sequence", Type: arrow.PrimitiveTypes.Int64}, {Name: "level", Type: p6DictType, Nullable: true}}
	case "empty":
		fields = []arrow.Field{}
	case "union":
		fields = []arrow.Field{
			{Name: "sequence", Type: arrow.PrimitiveTypes.Int64},
			{Name: "choice", Type: arrow.DenseUnionOf([]arrow.Field{
				{Name: "i", Type: arrow.PrimitiveTypes.Int32, Nullable: true},
				{Name: "s", Type: arrow.BinaryTypes.String, Nullable: true},
			}, []arrow.UnionTypeCode{0, 1})},
		}
	case "ree":
		fields = []arrow.Field{
			{Name: "sequence", Type: arrow.PrimitiveTypes.Int64},
			{Name: "run", Type: arrow.RunEndEncodedOf(arrow.PrimitiveTypes.Int32, arrow.BinaryTypes.String)},
		}
	case "invalid_utf8":
		fields = []arrow.Field{{Name: "sequence", Type: arrow.PrimitiveTypes.Int64}, {Name: "text", Type: arrow.BinaryTypes.String}}
	default:
		panic("unknown phase6 schema " + kind)
	}
	s, _ := p6Schemas.LoadOrStore(kind, arrow.NewSchema(fields, nil))
	return s.(*arrow.Schema)
}

func (r *p6Run) record(kind string, seq int64, rows, payload int) arrow.Record {
	schema := p6Schema(kind)
	mem := memory.DefaultAllocator
	if kind == "empty" {
		return array.NewRecord(schema, nil, int64(rows))
	}
	if kind == "dictionary" {
		return r.dictionaryRecord(schema, seq, rows)
	}
	b := array.NewRecordBuilder(mem, schema)
	defer b.Release()
	switch kind {
	case "basic":
		for i := seq; i < seq+int64(rows); i++ {
			b.Field(0).(*array.Int64Builder).Append(i)
			b.Field(1).(*array.Int64Builder).Append(9007199254740993 + i)
			b.Field(2).(*array.Float64Builder).Append(float64(i) / 4)
			b.Field(3).(*array.StringBuilder).Append("fixture <event> café 日志")
			b.Field(4).AppendNull()
			b.Field(5).(*array.TimestampBuilder).Append(arrow.Timestamp(1704067200000000 + i))
		}
	case "wide":
		pattern := p6Pattern(r.spec.Seed, payload)
		for i := seq; i < seq+int64(rows); i++ {
			b.Field(0).(*array.Int64Builder).Append(i)
			off := int(i % 64)
			b.Field(1).(*array.StringBuilder).Append(pattern[off : off+payload])
		}
	case "values":
		cols := p6ValueColumns()
		for i := seq; i < seq+int64(rows); i++ {
			for j, c := range cols {
				c.fill(b.Field(j), i)
			}
		}
	case "dup":
		for i := seq; i < seq+int64(rows); i++ {
			b.Field(0).(*array.Int64Builder).Append(i)
			b.Field(1).(*array.StringBuilder).Append(fmt.Sprintf("alpha-%d", i))
			b.Field(2).(*array.StringBuilder).Append(fmt.Sprintf("dup-last-%d", i))
			b.Field(3).(*array.Float64Builder).Append(float64(i) + 0.5)
			b.Field(4).(*array.Int64Builder).Append(i)
		}
	case "nan":
		for i := seq; i < seq+int64(rows); i++ {
			b.Field(0).(*array.Int64Builder).Append(i)
			v := float64(i) / 4
			switch i % 4 {
			case 1:
				v = math.NaN()
			case 3:
				v = math.Inf(1)
			}
			b.Field(1).(*array.Float64Builder).Append(v)
		}
	case "union":
		u := b.Field(1).(*array.DenseUnionBuilder)
		for i := seq; i < seq+int64(rows); i++ {
			b.Field(0).(*array.Int64Builder).Append(i)
			switch i % 3 {
			case 0:
				u.Append(0)
				u.Child(0).(*array.Int32Builder).Append(int32(i))
			case 1:
				u.Append(1)
				u.Child(1).(*array.StringBuilder).Append(fmt.Sprintf("u-%d", i))
			default:
				u.AppendNull()
			}
		}
	case "ree":
		ree := b.Field(1).(*array.RunEndEncodedBuilder)
		for i := seq; i < seq+int64(rows); i++ {
			b.Field(0).(*array.Int64Builder).Append(i)
		}
		for i := seq; i < seq+int64(rows); {
			n := min(int64(3), seq+int64(rows)-i)
			ree.Append(uint64(n))
			ree.ValueBuilder().(*array.StringBuilder).Append(fmt.Sprintf("run-%d", i/3))
			i += n
		}
	case "invalid_utf8":
		for i := seq; i < seq+int64(rows); i++ {
			b.Field(0).(*array.Int64Builder).Append(i)
			b.Field(1).(*array.StringBuilder).Append(string([]byte{'o', 'k', 0xff, 0xfe, byte('a' + i%26)}))
		}
	}
	return b.NewRecord()
}

// p6Pattern returns a deterministic printable string of payload+64 bytes.
func p6Pattern(seed int64, payload int) string {
	const alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
	buf := make([]byte, payload+64)
	x := uint64(seed)*0x9e3779b97f4a7c15 + 1
	for i := range buf {
		x ^= x << 13
		x ^= x >> 7
		x ^= x << 17
		buf[i] = alphabet[x%uint64(len(alphabet))]
	}
	return string(buf)
}

// Dictionaries: stable reuses one dictionary per subscription, replace sends a
// new dictionary per batch, delta grows it (emitted as IPC deltas) up to max_entries.
type p6DictState struct {
	dict       *array.String
	size       int
	generation int64
}

func (d *p6DictState) release() {
	if d.dict != nil {
		d.dict.Release()
	}
}

func (r *p6Run) dictConfig() p6Dictionary {
	cfg := p6Dictionary{Cardinality: 16, Mode: "stable", ValueBytes: 0, MaxEntries: 4096}
	if r.spec != nil && r.spec.Dictionary != nil {
		d := r.spec.Dictionary
		if d.Cardinality > 0 {
			cfg.Cardinality = d.Cardinality
		}
		if d.Mode != "" {
			cfg.Mode = d.Mode
		}
		cfg.ValueBytes = d.ValueBytes
		if d.MaxEntries > 0 {
			cfg.MaxEntries = d.MaxEntries
		}
	}
	return cfg
}

func p6DictValue(generation int64, i, width int) string {
	v := fmt.Sprintf("level-%d", i)
	if generation >= 0 {
		v = fmt.Sprintf("g%d-level-%d", generation, i)
	}
	if width > len(v) {
		v += strings.Repeat("a", width-len(v))
	}
	return v
}

func newDict(generation int64, size, width int) *array.String {
	b := array.NewStringBuilder(memory.DefaultAllocator)
	defer b.Release()
	for i := 0; i < size; i++ {
		b.Append(p6DictValue(generation, i, width))
	}
	return b.NewStringArray()
}

func (r *p6Run) dictionaryRecord(schema *arrow.Schema, seq int64, rows int) arrow.Record {
	cfg := r.dictConfig()
	switch {
	case r.dict == nil:
		size := cfg.Cardinality
		if cfg.Mode == "delta" || cfg.Mode == "cycle" {
			size = min(cfg.Cardinality, cfg.MaxEntries)
		}
		generation := int64(-1)
		switch cfg.Mode {
		case "replace":
			generation = r.subBatch
		case "cycle":
			generation = 0
		}
		r.dict = &p6DictState{dict: newDict(generation, size, cfg.ValueBytes), size: size, generation: generation}
		r.dictChange = "initial"
	case cfg.Mode == "replace":
		r.dict.release()
		r.dict.dict = newDict(r.subBatch, cfg.Cardinality, cfg.ValueBytes)
		r.dictChange = "replacement"
	case cfg.Mode == "delta" && r.dict.size < cfg.MaxEntries:
		r.dict.release()
		r.dict.size = min(r.dict.size+cfg.Cardinality, cfg.MaxEntries)
		r.dict.dict = newDict(-1, r.dict.size, cfg.ValueBytes)
		r.dictChange = "delta"
	case cfg.Mode == "cycle":
		// Grow by deltas to max_entries, then reset with a new generation
		// (an IPC replacement), for as long as the run lasts.
		r.dict.release()
		if r.dict.size >= cfg.MaxEntries {
			r.dict.generation++
			r.dict.size = min(cfg.Cardinality, cfg.MaxEntries)
			r.dictChange, r.dictReset = "replacement", true
		} else {
			r.dict.size = min(r.dict.size+cfg.Cardinality, cfg.MaxEntries)
			r.dictChange = "delta"
		}
		r.dict.dict = newDict(r.dict.generation, r.dict.size, cfg.ValueBytes)
	}
	seqs := array.NewInt64Builder(memory.DefaultAllocator)
	defer seqs.Release()
	idx := array.NewInt32Builder(memory.DefaultAllocator)
	defer idx.Release()
	for i := seq; i < seq+int64(rows); i++ {
		seqs.Append(i)
		if i%7 == 6 {
			idx.AppendNull()
		} else {
			idx.Append(int32(i % int64(r.dict.size)))
		}
	}
	seqArr, idxArr := seqs.NewArray(), idx.NewArray()
	defer seqArr.Release()
	defer idxArr.Release()
	level := array.NewDictionaryArray(p6DictType, idxArr, r.dict.dict)
	defer level.Release()
	return array.NewRecord(schema, []arrow.Array{seqArr, level}, int64(rows))
}

// Value conversion matrix.

type p6Column struct {
	field arrow.Field
	fill  func(b array.Builder, seq int64)
}

func table(name string, typ arrow.DataType, values ...any) p6Column {
	return p6Column{field: arrow.Field{Name: name, Type: typ, Nullable: true}, fill: func(b array.Builder, seq int64) {
		appendAny(b, values[int(seq%int64(len(values)))])
	}}
}

func mustDecimal128(s string, prec, scale int32) decimal128.Num {
	n, err := decimal128.FromString(s, prec, scale)
	if err != nil {
		panic(err)
	}
	return n
}

func mustDecimal256(s string, prec, scale int32) decimal256.Num {
	n, err := decimal256.FromString(s, prec, scale)
	if err != nil {
		panic(err)
	}
	return n
}

var p6ValueColumnsOnce = sync.OnceValue(buildValueColumns)

func p6ValueColumns() []p6Column { return p6ValueColumnsOnce() }

func buildValueColumns() []p6Column {
	negZero := math.Copysign(0, -1)
	d128 := &arrow.Decimal128Type{Precision: 38, Scale: 10}
	d128s := &arrow.Decimal128Type{Precision: 10, Scale: 2}
	d256 := &arrow.Decimal256Type{Precision: 76, Scale: 20}
	return []p6Column{
		{field: arrow.Field{Name: "sequence", Type: arrow.PrimitiveTypes.Int64}, fill: func(b array.Builder, seq int64) { b.(*array.Int64Builder).Append(seq) }},
		table("bool", arrow.FixedWidthTypes.Boolean, true, false, nil),
		table("int8", arrow.PrimitiveTypes.Int8, int8(math.MinInt8), int8(math.MaxInt8), int8(0), int8(-1), nil),
		table("int16", arrow.PrimitiveTypes.Int16, int16(math.MinInt16), int16(math.MaxInt16), int16(0), nil),
		table("int32", arrow.PrimitiveTypes.Int32, int32(math.MinInt32), int32(math.MaxInt32), int32(-7), nil),
		table("int64", arrow.PrimitiveTypes.Int64, int64(math.MinInt64), int64(math.MaxInt64), int64(9007199254740993), int64(-9007199254740993), int64(0), nil),
		table("uint8", arrow.PrimitiveTypes.Uint8, uint8(math.MaxUint8), uint8(0), nil),
		table("uint16", arrow.PrimitiveTypes.Uint16, uint16(math.MaxUint16), uint16(1), nil),
		table("uint32", arrow.PrimitiveTypes.Uint32, uint32(math.MaxUint32), uint32(2), nil),
		table("uint64", arrow.PrimitiveTypes.Uint64, uint64(math.MaxUint64), uint64(9007199254740993), uint64(0), nil),
		table("float16", arrow.FixedWidthTypes.Float16, float16.New(1.5), float16.New(0), float16.New(65504), float16.New(0.1), nil),
		table("float32", arrow.PrimitiveTypes.Float32, float32(0.1), float32(math.MaxFloat32), float32(1e-7), float32(negZero), float32(16777217), float32(1e21), float32(1e20), nil),
		table("float64", arrow.PrimitiveTypes.Float64, 0.1, 1e20, 1e21, 1e-6, 1e-7, negZero, 123456789.123, math.MaxFloat64, math.SmallestNonzeroFloat64, 9007199254740993.0, nil),
		table("utf8", arrow.BinaryTypes.String, "", "plain", "<html>&'\"", "  ", "control\x01\t\n\\/", "café 日志 😀", nil),
		table("large_utf8", arrow.BinaryTypes.LargeString, "large", "<&>", nil),
		table("binary", arrow.BinaryTypes.Binary, []byte{}, []byte{0, 1, 2, 255}, []byte("hello"), nil),
		table("large_binary", arrow.BinaryTypes.LargeBinary, []byte{0xfe}, nil),
		table("fixed_binary", &arrow.FixedSizeBinaryType{ByteWidth: 4}, []byte{0, 1, 2, 3}, []byte("abcd"), nil),
		table("date32", arrow.FixedWidthTypes.Date32, arrow.Date32(0), arrow.Date32(19723), arrow.Date32(-1), nil),
		table("date64", arrow.FixedWidthTypes.Date64, arrow.Date64(0), arrow.Date64(1704067200000), arrow.Date64(-86400000), nil),
		table("time32s", arrow.FixedWidthTypes.Time32s, arrow.Time32(0), arrow.Time32(86399), nil),
		table("time32ms", arrow.FixedWidthTypes.Time32ms, arrow.Time32(1), arrow.Time32(86399999), nil),
		table("time64us", arrow.FixedWidthTypes.Time64us, arrow.Time64(1), arrow.Time64(86399999999), nil),
		table("time64ns", arrow.FixedWidthTypes.Time64ns, arrow.Time64(1), arrow.Time64(86399999999999), nil),
		table("ts_s", &arrow.TimestampType{Unit: arrow.Second}, arrow.Timestamp(0), arrow.Timestamp(1704067200), arrow.Timestamp(-1), nil),
		table("ts_ms", &arrow.TimestampType{Unit: arrow.Millisecond}, arrow.Timestamp(1704067200123), nil),
		table("ts_us", &arrow.TimestampType{Unit: arrow.Microsecond}, arrow.Timestamp(1704067200123456), arrow.Timestamp(1704067200000000), nil),
		table("ts_ns", &arrow.TimestampType{Unit: arrow.Nanosecond}, arrow.Timestamp(1704067200123456789), arrow.Timestamp(1), nil),
		table("ts_us_utc", &arrow.TimestampType{Unit: arrow.Microsecond, TimeZone: "UTC"}, arrow.Timestamp(1704067200123456), nil),
		table("ts_us_kolkata", &arrow.TimestampType{Unit: arrow.Microsecond, TimeZone: "Asia/Kolkata"}, arrow.Timestamp(1704067200123456), nil),
		table("ts_ms_offset", &arrow.TimestampType{Unit: arrow.Millisecond, TimeZone: "+05:30"}, arrow.Timestamp(1704067200123), nil),
		table("duration_s", arrow.FixedWidthTypes.Duration_s, arrow.Duration(3600), arrow.Duration(-1), nil),
		table("duration_ms", arrow.FixedWidthTypes.Duration_ms, arrow.Duration(1500), nil),
		table("duration_us", arrow.FixedWidthTypes.Duration_us, arrow.Duration(1), nil),
		table("duration_ns", arrow.FixedWidthTypes.Duration_ns, arrow.Duration(math.MaxInt64), nil),
		table("decimal128", d128, mustDecimal128("1234567890123456789012345678.0123456789", 38, 10), mustDecimal128("-0.0000000001", 38, 10), mustDecimal128("0", 38, 10), nil),
		table("decimal128_small", d128s, mustDecimal128("12.50", 10, 2), mustDecimal128("-99999999.99", 10, 2), nil),
		table("decimal256", d256, mustDecimal256("12345678901234567890123456789012345678901234567890.12345678901234567890", 76, 20), mustDecimal256("-1.5", 76, 20), nil),
		table("interval_month", arrow.FixedWidthTypes.MonthInterval, arrow.MonthInterval(14), arrow.MonthInterval(-1), nil),
		table("interval_day_time", arrow.FixedWidthTypes.DayTimeInterval, arrow.DayTimeInterval{Days: 2, Milliseconds: 1500}, nil),
		table("interval_mdn", arrow.FixedWidthTypes.MonthDayNanoInterval, arrow.MonthDayNanoInterval{Months: 1, Days: -2, Nanoseconds: 3000000001}, nil),
		table("null", arrow.Null, nil),
		{field: arrow.Field{Name: "list_int32", Type: arrow.ListOf(arrow.PrimitiveTypes.Int32), Nullable: true}, fill: func(b array.Builder, seq int64) {
			lb := b.(*array.ListBuilder)
			switch seq % 3 {
			case 0:
				lb.Append(true)
				vb := lb.ValueBuilder().(*array.Int32Builder)
				vb.Append(int32(seq))
				vb.AppendNull()
				vb.Append(-1)
			case 1:
				lb.Append(true)
			default:
				lb.AppendNull()
			}
		}},
		{field: arrow.Field{Name: "large_list_utf8", Type: arrow.LargeListOf(arrow.BinaryTypes.String), Nullable: true}, fill: func(b array.Builder, seq int64) {
			lb := b.(*array.LargeListBuilder)
			if seq%2 == 0 {
				lb.Append(true)
				lb.ValueBuilder().(*array.StringBuilder).Append("x<y")
			} else {
				lb.AppendNull()
			}
		}},
		{field: arrow.Field{Name: "fixed_list_int8", Type: arrow.FixedSizeListOf(2, arrow.PrimitiveTypes.Int8), Nullable: true}, fill: func(b array.Builder, seq int64) {
			lb := b.(*array.FixedSizeListBuilder)
			if seq%2 == 0 {
				lb.Append(true)
				vb := lb.ValueBuilder().(*array.Int8Builder)
				vb.Append(int8(seq % 100))
				vb.Append(-8)
			} else {
				lb.AppendNull()
			}
		}},
		{field: arrow.Field{Name: "struct", Type: arrow.StructOf(arrow.Field{Name: "b", Type: arrow.BinaryTypes.String, Nullable: true},
			arrow.Field{Name: "a", Type: arrow.PrimitiveTypes.Int32, Nullable: true}), Nullable: true}, fill: func(b array.Builder, seq int64) {
			sb := b.(*array.StructBuilder)
			if seq%3 == 2 {
				sb.AppendNull()
				return
			}
			sb.Append(true)
			sb.FieldBuilder(0).(*array.StringBuilder).Append(fmt.Sprintf("s%d", seq))
			if seq%3 == 1 {
				sb.FieldBuilder(1).AppendNull()
			} else {
				sb.FieldBuilder(1).(*array.Int32Builder).Append(int32(seq))
			}
		}},
		{field: arrow.Field{Name: "map", Type: arrow.MapOf(arrow.BinaryTypes.String, arrow.PrimitiveTypes.Int32), Nullable: true}, fill: func(b array.Builder, seq int64) {
			mb := b.(*array.MapBuilder)
			if seq%2 == 1 {
				mb.AppendNull()
				return
			}
			mb.Append(true)
			mb.KeyBuilder().(*array.StringBuilder).Append("z")
			mb.ItemBuilder().(*array.Int32Builder).Append(1)
			mb.KeyBuilder().(*array.StringBuilder).Append("a")
			mb.ItemBuilder().(*array.Int32Builder).Append(int32(seq))
		}},
		{field: arrow.Field{Name: "dict_utf8", Type: p6DictType, Nullable: true}, fill: func(b array.Builder, seq int64) {
			db := b.(*array.BinaryDictionaryBuilder)
			if seq%4 == 3 {
				db.AppendNull()
				return
			}
			_ = db.AppendString([]string{"info", "warn", "<error>"}[seq%3])
		}},
	}
}

func appendAny(b array.Builder, v any) {
	if v == nil {
		b.AppendNull()
		return
	}
	switch b := b.(type) {
	case *array.BooleanBuilder:
		b.Append(v.(bool))
	case *array.Int8Builder:
		b.Append(v.(int8))
	case *array.Int16Builder:
		b.Append(v.(int16))
	case *array.Int32Builder:
		b.Append(v.(int32))
	case *array.Int64Builder:
		b.Append(v.(int64))
	case *array.Uint8Builder:
		b.Append(v.(uint8))
	case *array.Uint16Builder:
		b.Append(v.(uint16))
	case *array.Uint32Builder:
		b.Append(v.(uint32))
	case *array.Uint64Builder:
		b.Append(v.(uint64))
	case *array.Float16Builder:
		b.Append(v.(float16.Num))
	case *array.Float32Builder:
		b.Append(v.(float32))
	case *array.Float64Builder:
		b.Append(v.(float64))
	case *array.StringBuilder:
		b.Append(v.(string))
	case *array.LargeStringBuilder:
		b.Append(v.(string))
	case *array.BinaryBuilder:
		b.Append(v.([]byte))
	case *array.FixedSizeBinaryBuilder:
		b.Append(v.([]byte))
	case *array.Date32Builder:
		b.Append(v.(arrow.Date32))
	case *array.Date64Builder:
		b.Append(v.(arrow.Date64))
	case *array.Time32Builder:
		b.Append(v.(arrow.Time32))
	case *array.Time64Builder:
		b.Append(v.(arrow.Time64))
	case *array.TimestampBuilder:
		b.Append(v.(arrow.Timestamp))
	case *array.DurationBuilder:
		b.Append(v.(arrow.Duration))
	case *array.Decimal128Builder:
		b.Append(v.(decimal128.Num))
	case *array.Decimal256Builder:
		b.Append(v.(decimal256.Num))
	case *array.MonthIntervalBuilder:
		b.Append(v.(arrow.MonthInterval))
	case *array.DayTimeIntervalBuilder:
		b.Append(v.(arrow.DayTimeInterval))
	case *array.MonthDayNanoIntervalBuilder:
		b.Append(v.(arrow.MonthDayNanoInterval))
	default:
		panic(fmt.Sprintf("phase6: unsupported builder %T", b))
	}
}

// TLS: a per-run self-signed CA and leaf; clients trust ca_path (SSL_CERT_FILE).

func (p *phase6) generateTLS() error {
	caKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return err
	}
	now := time.Now()
	ca := &x509.Certificate{
		SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "pb-fixture phase6 CA"},
		NotBefore: now.Add(-time.Hour), NotAfter: now.Add(24 * time.Hour), IsCA: true, BasicConstraintsValid: true,
		KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature,
	}
	caDER, err := x509.CreateCertificate(rand.Reader, ca, ca, &caKey.PublicKey, caKey)
	if err != nil {
		return err
	}
	caCert, err := x509.ParseCertificate(caDER)
	if err != nil {
		return err
	}
	leafKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return err
	}
	leaf := &x509.Certificate{
		SerialNumber: big.NewInt(2), Subject: pkix.Name{CommonName: "pb-fixture"},
		NotBefore: now.Add(-time.Hour), NotAfter: now.Add(24 * time.Hour), KeyUsage: x509.KeyUsageDigitalSignature,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}
	names := p.cfg.TLS.Names
	if len(names) == 0 {
		names = []string{"localhost", "127.0.0.1", "::1"}
	}
	for _, name := range names {
		if ip := net.ParseIP(name); ip != nil {
			leaf.IPAddresses = append(leaf.IPAddresses, ip)
		} else {
			leaf.DNSNames = append(leaf.DNSNames, name)
		}
	}
	leafDER, err := x509.CreateCertificate(rand.Reader, leaf, caCert, &leafKey.PublicKey, caKey)
	if err != nil {
		return err
	}
	p.caPEM = pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: caDER})
	p.tlsCert = &tls.Certificate{Certificate: [][]byte{leafDER, caDER}, PrivateKey: leafKey}
	return nil
}

func tlsVersion(name string, fallback uint16) uint16 {
	switch name {
	case "1.0":
		return tls.VersionTLS10
	case "1.1":
		return tls.VersionTLS11
	case "1.2":
		return tls.VersionTLS12
	case "1.3":
		return tls.VersionTLS13
	}
	return fallback
}

func (p *phase6) tlsConfig(alpn []string) *tls.Config {
	cfg := &tls.Config{
		Certificates: []tls.Certificate{*p.tlsCert},
		MinVersion:   tlsVersion(p.cfg.TLS.MinVersion, tls.VersionTLS10), MaxVersion: tlsVersion(p.cfg.TLS.MaxVersion, tls.VersionTLS13),
	}
	if !p.cfg.TLS.NoALPN {
		cfg.NextProtos = alpn
	}
	return cfg
}

func (p *phase6) handshakeEvent(protocol string, conn *tls.Conn, err error, extra ...p6Fields) {
	fields := p6Fields{"protocol": protocol, "remote": conn.RemoteAddr().String(), "error": errString(err)}
	for _, e := range extra {
		for k, v := range e {
			fields[k] = v
		}
	}
	if err == nil {
		st := conn.ConnectionState()
		fields["server_name"], fields["alpn"], fields["version"] = st.ServerName, st.NegotiatedProtocol, tls.VersionName(st.Version)
	}
	p.emit(monotonicNS(), "tls_handshake", fields)
}

// p6Creds is a server-only gRPC TLS credential that, unlike credentials.NewTLS,
// can omit ALPN and records every handshake outcome.
type p6Creds struct {
	p   *phase6
	cfg *tls.Config
}

func (c *p6Creds) ClientHandshake(context.Context, string, net.Conn) (net.Conn, credentials.AuthInfo, error) {
	return nil, nil, errors.New("pb-fixture: server-only credentials")
}

func (c *p6Creds) ServerHandshake(raw net.Conn) (net.Conn, credentials.AuthInfo, error) {
	conn := tls.Server(raw, c.cfg)
	err := conn.Handshake()
	if err != nil {
		reset := false
		if pc, ok := raw.(*p6Conn); ok && pc.tcp != nil && c.p.cfg.TLS.ResetInvalidHandshake {
			reset = pc.tcp.SetLinger(0) == nil
		}
		c.p.handshakeEvent("grpc", conn, err, p6Fields{"reset": reset})
		_ = conn.Close()
		return nil, nil, err
	}
	c.p.handshakeEvent("grpc", conn, err)
	// Frame-level ops must write above TLS; resets still use the TCP socket.
	tracked := raw
	if pc, ok := raw.(*p6Conn); ok {
		tracked = c.p.track(&p6Conn{Conn: conn, tcp: pc.tcp, p: c.p})
	}
	return tracked, credentials.TLSInfo{State: conn.ConnectionState(), CommonAuthInfo: credentials.CommonAuthInfo{SecurityLevel: credentials.PrivacyAndIntegrity}}, nil
}

func (c *p6Creds) Info() credentials.ProtocolInfo {
	return credentials.ProtocolInfo{SecurityProtocol: "tls", SecurityVersion: "1.2"}
}

func (c *p6Creds) Clone() credentials.TransportCredentials {
	return &p6Creds{p: c.p, cfg: c.cfg.Clone()}
}
func (c *p6Creds) OverrideServerName(string) error { return nil }

type p6TLSListener struct {
	net.Listener
	p   *phase6
	cfg *tls.Config
}

func (l *p6TLSListener) Accept() (net.Conn, error) {
	raw, err := l.Listener.Accept()
	if err != nil {
		return nil, err
	}
	conn := tls.Server(raw, l.cfg)
	// tls.Conn serializes handshakes, so net/http's own read reuses this result.
	go func() { l.p.handshakeEvent("http", conn, conn.Handshake()) }()
	return conn, nil
}

// Connection registry for transport-loss ops. p6Conn serializes writes so a
// GOAWAY written by a script never splits a gRPC write; the settle delay
// before an op lets grpc's writer go idle on a frame boundary.

type p6Conn struct {
	net.Conn
	tcp    *net.TCPConn
	p      *phase6
	wmu    sync.Mutex
	closed sync.Once
}

func (c *p6Conn) Write(b []byte) (int, error) {
	c.wmu.Lock()
	defer c.wmu.Unlock()
	return c.Conn.Write(b)
}

func (c *p6Conn) Close() error {
	c.closed.Do(func() {
		c.p.mu.Lock()
		if c.p.conns[c.RemoteAddr().String()] == c {
			delete(c.p.conns, c.RemoteAddr().String())
		}
		c.p.mu.Unlock()
	})
	return c.Conn.Close()
}

func (p *phase6) track(c *p6Conn) *p6Conn {
	p.mu.Lock()
	p.conns[c.RemoteAddr().String()] = c
	p.mu.Unlock()
	return c
}

type p6Listener struct {
	net.Listener
	p *phase6
}

func (l *p6Listener) Accept() (net.Conn, error) {
	conn, err := l.Listener.Accept()
	if err != nil {
		return nil, err
	}
	tcp, _ := conn.(*net.TCPConn)
	return l.p.track(&p6Conn{Conn: conn, tcp: tcp, p: l.p}), nil
}

// connAction ends the subscription by breaking its transport: a FIN without
// GOAWAY, an abortive RST (SO_LINGER=0), or a raw GOAWAY frame then a FIN.
// The listener stays up, so a reconnect gets a new DoGet.
func (r *p6Run) connAction(op *p6Op) error {
	settle := 50
	if op.SettleMS != nil {
		settle = *op.SettleMS
	}
	if !pause(r.ctx, settle) {
		return r.finish(r.ctx.Err(), "client_cancel")
	}
	r.p.mu.Lock()
	c := r.p.conns[r.remote]
	r.p.mu.Unlock()
	if c == nil || c.tcp == nil {
		return r.finish(status.Error(codes.Internal, "fixture: no tracked connection for "+r.remote), "fixture_error")
	}
	fields := p6Fields{"action": op.Op, "remote": r.remote}
	switch op.Op {
	case "conn_close":
		_ = c.tcp.Close()
	case "conn_reset":
		_ = c.tcp.SetLinger(0)
		_ = c.tcp.Close()
	case "goaway":
		var frame bytes.Buffer
		_ = http2.NewFramer(&frame, nil).WriteGoAway(op.LastStreamID, http2.ErrCode(op.ErrorCode), []byte(op.Debug))
		_, err := c.Write(frame.Bytes())
		fields["last_stream_id"], fields["error_code"], fields["write_error"] = op.LastStreamID, op.ErrorCode, errString(err)
		r.p.emit(monotonicNS(), "conn_action", r.fields(fields))
		if op.CloseAfterMS > 0 {
			t := time.NewTimer(time.Duration(op.CloseAfterMS) * time.Millisecond)
			select {
			case <-r.ctx.Done():
			case <-t.C:
			}
			t.Stop()
		}
		_ = c.tcp.Close()
		fields = p6Fields{"action": "close_after_goaway", "remote": r.remote}
	}
	r.p.emit(monotonicNS(), "conn_action", r.fields(fields))
	select {
	case <-r.ctx.Done():
	case <-time.After(5 * time.Second):
	}
	return r.finish(status.Error(codes.Canceled, "fixture: "+op.Op), op.Op)
}

// Extra endpoints.

func (p *phase6) startEndpoints(f *fixture) (map[string]int, error) {
	ports := map[string]int{}
	for _, e := range p.cfg.Endpoints {
		l, err := net.Listen("tcp", net.JoinHostPort(p.listenHost(), "0"))
		if err != nil {
			return nil, err
		}
		port := l.Addr().(*net.TCPAddr).Port
		ports[e.Name] = port
		p.mu.Lock()
		p.ports[e.Name] = port
		p.mu.Unlock()
		switch e.Kind {
		case "blackhole":
			go p.blackhole(e, l)
		case "raw_h2":
			go p.rawH2(f, e, l)
		case "http_status":
			handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				p.emit(monotonicNS(), "endpoint_request", p6Fields{
					"name": e.Name, "method": r.Method, "path": r.URL.Path,
					"proto": r.Proto, "content_type": r.Header.Get("Content-Type"), "authority": r.Host,
				})
				if e.ContentType != "" {
					w.Header().Set("Content-Type", e.ContentType)
				}
				code := e.Status
				if code == 0 {
					code = http.StatusBadGateway
				}
				w.WriteHeader(code)
				_, _ = io.WriteString(w, e.Body)
			})
			h2 := &http2.Server{}
			if e.TLS {
				server := &http.Server{Handler: handler, TLSConfig: p.tlsConfig([]string{"h2"})}
				if err := http2.ConfigureServer(server, h2); err != nil {
					return nil, err
				}
				go func() { _ = server.ServeTLS(l, "", "") }()
			} else {
				go func() { _ = http.Serve(l, h2c.NewHandler(handler, h2)) }()
			}
		}
	}
	return ports, nil
}

func (p *phase6) blackhole(e *p6Endpoint, l net.Listener) {
	hold := time.Duration(e.HoldMS) * time.Millisecond
	if hold <= 0 {
		hold = 60 * time.Second
	}
	for {
		conn, err := l.Accept()
		if err != nil {
			return
		}
		go func() {
			start := monotonicNS()
			p.emit(start, "endpoint_accept", p6Fields{"name": e.Name, "remote": conn.RemoteAddr().String()})
			_ = conn.SetDeadline(time.Now().Add(hold))
			var reader io.Reader = conn
			if e.TLS {
				tc := tls.Server(conn, p.tlsConfig([]string{"h2"}))
				err := tc.Handshake()
				p.handshakeEvent("blackhole", tc, err)
				reader = tc
			}
			n, err := io.Copy(io.Discard, reader)
			_ = conn.Close()
			p.emit(monotonicNS(), "endpoint_close", p6Fields{"name": e.Name, "bytes_read": n, "duration_ns": monotonicNS() - start, "error": errString(err)})
		}()
	}
}

// HTTP accounting. observeHTTP counts every non-control request and records the
// discovery milestone: about_completed_ns is when the first 2xx
// /api/v1/about handler returned (headers and full body written).

type p6ResponseWriter struct {
	http.ResponseWriter
	code int
}

func (w *p6ResponseWriter) WriteHeader(code int) {
	if w.code == 0 {
		w.code = code
	}
	w.ResponseWriter.WriteHeader(code)
}

func (w *p6ResponseWriter) Write(b []byte) (int, error) {
	if w.code == 0 {
		w.code = http.StatusOK
	}
	return w.ResponseWriter.Write(b)
}

func (w *p6ResponseWriter) Flush() {
	if f, ok := w.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}

const p6AboutPath = "/api/v1/about"

func (p *phase6) observeHTTP(w http.ResponseWriter, r *http.Request, received int64, headers map[string]string, index int) (http.ResponseWriter, func()) {
	about := r.URL.Path == p6AboutPath
	p.mu.Lock()
	p.httpRequests++
	if about {
		p.aboutRequests++
		if p.firstAbout == nil {
			p.firstAbout = &received
		}
		last := received
		p.lastAbout = &last
	}
	p.mu.Unlock()
	p.emit(received, "http", p6Fields{"method": r.Method, "path": r.URL.RequestURI(), "headers": headers, "request": index})
	rw := &p6ResponseWriter{ResponseWriter: w}
	return rw, func() {
		if !about || rw.code < 200 || rw.code > 299 {
			return
		}
		done := monotonicNS()
		p.mu.Lock()
		if p.aboutCompleted == nil {
			p.aboutCompleted = &done
		}
		p.mu.Unlock()
	}
}

// raw_h2: a scripted HTTP/2 gRPC responder. Header fields and frames are sent
// verbatim, unlike grpc-go, which sanitizes grpc-message and refuses invalid
// grpc-status-details-bin. Flow control is not modeled: keep a stream's DATA
// under the client's initial 64 KiB window.

func (e *p6Endpoint) acts(n int64) []p6Act {
	for _, sub := range e.Subscriptions {
		repeat := int64(max(sub.Repeat, 1))
		if n < repeat {
			return sub.Acts
		}
		n -= repeat
	}
	return nil
}

func (p *phase6) rawH2(f *fixture, e *p6Endpoint, l net.Listener) {
	for {
		conn, err := l.Accept()
		if err != nil {
			return
		}
		go p.rawConn(f, e, conn)
	}
}

type p6RawConn struct {
	p      *phase6
	f      *fixture
	e      *p6Endpoint
	raw    net.Conn
	conn   net.Conn
	wmu    sync.Mutex
	w      *http2.Framer
	done   chan struct{}
	remote string
}

func (c *p6RawConn) write(fn func(*http2.Framer) error) error {
	c.wmu.Lock()
	defer c.wmu.Unlock()
	return fn(c.w)
}

func (p *phase6) rawConn(f *fixture, e *p6Endpoint, raw net.Conn) {
	c := &p6RawConn{p: p, f: f, e: e, raw: raw, conn: raw, done: make(chan struct{}), remote: raw.RemoteAddr().String()}
	defer close(c.done)
	defer raw.Close()
	if e.TLS {
		tc := tls.Server(raw, p.tlsConfig([]string{"h2"}))
		err := tc.Handshake()
		p.handshakeEvent("raw_h2", tc, err)
		if err != nil {
			return
		}
		c.conn = tc
	}
	p.mu.Lock()
	p.stats("endpoint:"+e.Name).Connections++
	p.mu.Unlock()
	p.emit(monotonicNS(), "raw_h2_conn", p6Fields{"name": e.Name, "remote": c.remote})
	defer func() { p.emit(monotonicNS(), "raw_h2_conn_end", p6Fields{"name": e.Name, "remote": c.remote}) }()
	preface := make([]byte, len(http2.ClientPreface))
	if _, err := io.ReadFull(c.conn, preface); err != nil || string(preface) != http2.ClientPreface {
		return
	}
	c.w = http2.NewFramer(c.conn, nil)
	reader := http2.NewFramer(nil, c.conn)
	reader.ReadMetaHeaders = hpack.NewDecoder(4096, nil)
	reader.MaxHeaderListSize = 16 << 20
	if c.write(func(w *http2.Framer) error { return w.WriteSettings() }) != nil {
		return
	}
	type pending struct {
		md       map[string][]string
		received int64
		body     bytes.Buffer
	}
	streams := map[uint32]*pending{}
	for {
		frame, err := reader.ReadFrame()
		if err != nil {
			return
		}
		switch fr := frame.(type) {
		case *http2.SettingsFrame:
			if !fr.IsAck() {
				_ = c.write(func(w *http2.Framer) error { return w.WriteSettingsAck() })
			}
		case *http2.PingFrame:
			if !fr.IsAck() {
				_ = c.write(func(w *http2.Framer) error { return w.WritePing(true, fr.Data) })
			}
		case *http2.MetaHeadersFrame:
			md := map[string][]string{}
			for _, hf := range fr.Fields {
				md[hf.Name] = append(md[hf.Name], hf.Value)
			}
			streams[fr.StreamID] = &pending{md: md, received: monotonicNS()}
			if fr.StreamEnded() {
				go c.serve(fr.StreamID, md, streams[fr.StreamID].received, nil)
				delete(streams, fr.StreamID)
			}
		case *http2.DataFrame:
			if st := streams[fr.StreamID]; st != nil {
				st.body.Write(fr.Data())
				if fr.StreamEnded() {
					go c.serve(fr.StreamID, st.md, st.received, st.body.Bytes())
					delete(streams, fr.StreamID)
				}
			}
		case *http2.RSTStreamFrame:
			p.emit(monotonicNS(), "raw_h2_client_rst", p6Fields{"name": e.Name, "stream_id": fr.StreamID, "code": fr.ErrCode.String()})
		}
	}
}

// grpcFrame prefixes one gRPC length-delimited message.
func grpcFrame(msg []byte) []byte {
	out := make([]byte, 5, 5+len(msg))
	binary.BigEndian.PutUint32(out[1:], uint32(len(msg)))
	return append(out, msg...)
}

func (c *p6RawConn) data(id uint32, b []byte, end bool) error {
	const maxFrame = 16384
	for {
		n := min(len(b), maxFrame)
		last := n == len(b)
		chunk := b[:n]
		if err := c.write(func(w *http2.Framer) error { return w.WriteData(id, end && last, chunk) }); err != nil {
			return err
		}
		if last {
			return nil
		}
		b = b[n:]
	}
}

func (c *p6RawConn) headers(id uint32, fields [][2]string, end bool) error {
	var block bytes.Buffer
	enc := hpack.NewEncoder(&block)
	for _, f := range fields {
		_ = enc.WriteField(hpack.HeaderField{Name: f[0], Value: f[1]})
	}
	b := block.Bytes()
	const maxFrame = 16384
	return c.write(func(w *http2.Framer) error {
		first := min(len(b), maxFrame)
		if err := w.WriteHeaders(http2.HeadersFrameParam{StreamID: id, BlockFragment: b[:first], EndStream: end, EndHeaders: first == len(b)}); err != nil {
			return err
		}
		for rest := b[first:]; len(rest) > 0; {
			n := min(len(rest), maxFrame)
			if err := w.WriteContinuation(id, n == len(rest), rest[:n]); err != nil {
				return err
			}
			rest = rest[n:]
		}
		return nil
	})
}

func (c *p6RawConn) serve(id uint32, md map[string][]string, received int64, body []byte) {
	p, e := c.p, c.e
	key := "endpoint:" + e.Name
	ticket := []byte(nil)
	if len(body) >= 5 {
		var t flight.Ticket
		if proto.Unmarshal(body[5:], &t) == nil {
			ticket = t.Ticket
		}
	}
	first := func(name string) string {
		if v := md[name]; len(v) > 0 {
			return v[0]
		}
		return ""
	}
	contract := map[string]string{}
	for _, h := range contractHeaders {
		if v := md[h]; len(v) > 0 {
			contract[h] = strings.Join(v, ",")
		}
	}
	var parsed struct {
		Stream string `json:"stream"`
	}
	_ = json.Unmarshal(ticket, &parsed)
	expected, _ := json.Marshal(map[string]string{"stream": parsed.Stream})
	authorized := c.f.authorized(contract)
	if p.cfg.FlightAuth != nil {
		authorized = true
		for k, v := range p.cfg.FlightAuth {
			if values := md[k]; len(values) != 1 || values[0] != v {
				authorized = false
			}
		}
	}
	c.f.record(request{Protocol: "flight", Method: "DoGet", Path: first(":path"), Headers: contract, Body: string(ticket), TimeUnixNS: time.Now().UnixNano()})
	p.mu.Lock()
	e.next++
	ordinal := e.next - 1
	p.dogets++
	doget := p.dogets - 1
	stats := p.stats(key)
	stats.DoGets++
	if stats.LastTerminal != nil {
		stats.ReconnectGapNS.add(received - stats.LastTerminal.TNS)
	}
	if stats.FirstDoGetNS == nil {
		stats.FirstDoGetNS = &received
	}
	last := received
	stats.LastDoGetNS = &last
	v := &stats.RequestValidation
	v.AuthChecks++
	v.TicketChecks++
	if !authorized {
		v.InvalidAuth++
	}
	if !bytes.Equal(ticket, expected) {
		v.TicketMismatches++
	}
	if p.aboutCompleted == nil || *p.aboutCompleted > received {
		v.DoGetBeforeAbout++
	}
	authority := first(":authority")
	if _, seen := stats.Authorities[authority]; seen || len(stats.Authorities) < 16 {
		stats.Authorities[authority]++
	} else {
		stats.Authorities["<other>"]++
	}
	acts := e.acts(ordinal)
	if acts != nil {
		stats.Subscriptions++
	}
	p.mu.Unlock()
	fields := p6Fields{
		"name": e.Name, "stream": key, "ordinal": ordinal, "sub": ordinal, "doget": doget, "stream_id": id,
		"ticket": string(ticket), "ticket_b64": base64.StdEncoding.EncodeToString(ticket), "ticket_matches": bytes.Equal(ticket, expected),
		"metadata": md, "authority": authority, "authorized": authorized, "peer": c.remote, "unexpected": acts == nil,
	}
	p.emit(received, "raw_h2_stream", fields)
	run := &p6Run{p: p, spec: &p6Stream{Schema: e.Schema, Seed: 1}, name: key, sub: ordinal, doget: doget}
	finish := func(code, message, reason string) {
		t := monotonicNS()
		p.mu.Lock()
		stats.Terminals[code]++
		stats.LastTerminal = &p6Terminal{Code: code, Message: message, Reason: reason, Sub: ordinal, TNS: t}
		p.mu.Unlock()
		p.emit(t, "terminal", run.fields(p6Fields{"code": code, "message": message, "reason": reason, "name": e.Name}))
		p.writeSummary()
	}
	if acts == nil {
		_ = c.headers(id, [][2]string{
			{":status", "200"},
			{"content-type", "application/grpc"},
			{"grpc-status", "3"},
			{"grpc-message", "fixture subscriptions exhausted"},
		}, true)
		finish("InvalidArgument", "fixture subscriptions exhausted", "exhausted")
		return
	}
	for i := range acts {
		a := &acts[i]
		var err error
		switch a.Act {
		case "headers":
			err = c.headers(id, a.Fields, a.EndStream)
		case "trailers":
			err = c.headers(id, a.Fields, true)
		case "schema":
			var msgs []p6Message
			if msgs, err = run.encodeWith(p6Schema(e.Schema), nil); err == nil {
				for _, m := range msgs {
					b, _ := proto.Marshal(m.fd)
					if err = c.data(id, grpcFrame(b), false); err != nil {
						break
					}
				}
				run.schemaSent = true
			}
		case "batch":
			err = c.batch(run, stats, id, a.Rows)
		case "data":
			b, _ := base64.StdEncoding.DecodeString(a.B64)
			err = c.data(id, b, a.EndStream)
		case "end_stream":
			err = c.write(func(w *http2.Framer) error { return w.WriteData(id, true, nil) })
		case "rst":
			err = c.write(func(w *http2.Framer) error { return w.WriteRSTStream(id, http2.ErrCode(a.Code)) })
		case "goaway":
			lastID := uint32(0)
			var lv any
			_ = json.Unmarshal(a.Last, &lv)
			switch x := lv.(type) {
			case float64:
				lastID = uint32(x)
			case string:
				if x == "ours" {
					lastID = id
				} else if id > 2 {
					lastID = id - 2
				}
			}
			err = c.write(func(w *http2.Framer) error { return w.WriteGoAway(lastID, http2.ErrCode(a.Code), []byte(a.Debug)) })
		case "close":
			err = c.raw.Close() // TCP FIN without TLS close_notify
		case "sleep":
			t := time.NewTimer(time.Duration(a.MS) * time.Millisecond)
			select {
			case <-c.done:
			case <-t.C:
			}
			t.Stop()
		}
		p.emit(monotonicNS(), "raw_h2_act", p6Fields{"name": e.Name, "stream": key, "ordinal": ordinal, "act": a.Act, "error": errString(err)})
		if err != nil {
			finish("Unknown", err.Error(), "send_error")
			return
		}
	}
	finish(rawTerminalCode(&acts[len(acts)-1]), "", "script")
}

// rawTerminalCode names what the script sent: the gRPC code for a numeric
// grpc-status, otherwise "raw:<act>" or "raw:grpc-status=<value>".
func rawTerminalCode(a *p6Act) string {
	if a.Act == "trailers" || a.Act == "headers" {
		for _, f := range a.Fields {
			if f[0] == "grpc-status" {
				if n, err := strconv.Atoi(f[1]); err == nil && n >= 0 {
					return codes.Code(n).String()
				}
				return "raw:grpc-status=" + f[1]
			}
		}
		return "raw:" + a.Act + "-without-grpc-status"
	}
	return "raw:" + a.Act
}

func (c *p6RawConn) batch(run *p6Run, stats *p6StreamStats, id uint32, rows int) error {
	p := c.p
	run.ensureWriter()
	p.mu.Lock()
	seq := stats.NextSequence
	p.mu.Unlock()
	rec := run.record(run.spec.Schema, seq, rows, 0)
	defer rec.Release()
	if err := run.writer.Write(rec); err != nil {
		return err
	}
	var out []byte
	encoded := 0
	for _, m := range run.capture.take() {
		if m.typ == ipc.MessageSchema && run.schemaSent {
			continue
		}
		b, err := proto.Marshal(m.fd)
		if err != nil {
			return err
		}
		encoded += len(b)
		out = append(out, grpcFrame(b)...)
	}
	run.schemaSent = true
	var expected []byte
	if p.cfg.ExpectedOutput {
		var buf bytes.Buffer
		_ = array.RecordToJSON(rec, &buf)
		buf.WriteByte('\n')
		expected = buf.Bytes()
	}
	start := monotonicNS()
	if err := c.data(id, out, false); err != nil {
		return err
	}
	p.mu.Lock()
	if rows > 0 && stats.FirstNonemptyBatchNS == nil {
		stats.FirstNonemptyBatchNS = &start
	}
	stats.Batches++
	if rows == 0 {
		stats.ZeroRowBatches++
	}
	stats.Rows += int64(rows)
	stats.NextSequence += int64(rows)
	stats.EncodedBytes += int64(encoded)
	if stats.hash != nil {
		stats.hash.Write(expected)
		stats.stdoutBytes += int64(len(expected))
		stats.renderedLines += int64(bytes.Count(expected, []byte{'\n'}) - 1)
		stats.ExpectedRows += int64(rows)
	}
	p.mu.Unlock()
	return nil
}
