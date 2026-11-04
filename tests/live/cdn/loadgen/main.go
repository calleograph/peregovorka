// loadgen — зрители HLS для нагрузочных испытаний узлов CDN Peregovorka (tests/live/cdn).
// Каждый зритель ведёт себя как плеер: опрашивает плейлист (раз в длительность последнего сегмента), скачивает новые сегменты по порядку
// и моделирует буфер воспроизведения (старт после 2 сегментов, остановка — буфер опустел). Итог — JSON: зрители, Мбит/с, время получения
// сегмента (p50/p95/p99), остановки, ошибки по кодам. Отдельное соединение keep-alive на зрителя, как у браузера.
package main

import (
	"bufio"
	"context"
	"crypto/tls"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"math/rand"
	"net"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

type stats struct {
	mu        sync.Mutex
	segMs     []float64
	plMs      []float64
	codes     map[string]int
	bytes     atomic.Int64
	reqs      atomic.Int64
	stalls    atomic.Int64
	stallMs   atomic.Int64
	active    atomic.Int64
	started   atomic.Int64
	ageMs     []float64
}

func (s *stats) code(c string) { s.mu.Lock(); s.codes[c]++; s.mu.Unlock() }

func pct(v []float64, p float64) float64 {
	if len(v) == 0 {
		return 0
	}
	c := append([]float64(nil), v...)
	sort.Float64s(c)
	i := int(p * float64(len(c)-1))
	return c[i]
}

type seg struct {
	seq int
	dur float64
	uri string
}

func parse(body string) (int, []seg) {
	ms := 0
	var out []seg
	var dur float64
	sc := bufio.NewScanner(strings.NewReader(body))
	n := 0
	for sc.Scan() {
		l := strings.TrimSpace(sc.Text())
		switch {
		case strings.HasPrefix(l, "#EXT-X-MEDIA-SEQUENCE:"):
			ms, _ = strconv.Atoi(strings.TrimPrefix(l, "#EXT-X-MEDIA-SEQUENCE:"))
		case strings.HasPrefix(l, "#EXTINF:"):
			dur, _ = strconv.ParseFloat(strings.TrimSuffix(strings.TrimPrefix(l, "#EXTINF:"), ","), 64)
		case l != "" && !strings.HasPrefix(l, "#"):
			out = append(out, seg{seq: ms + n, dur: dur, uri: l})
			n++
		}
	}
	return ms, out
}

var hdrName, hdrValue string

func get(ctx context.Context, u string) *http.Request {
	req, _ := http.NewRequestWithContext(ctx, "GET", u, nil)
	if hdrName != "" {
		req.Header.Set(hdrName, hdrValue)
	}
	return req
}

func viewer(ctx context.Context, id int, url string, client *http.Client, st *stats, firstSeen *sync.Map) {
	base := url[:strings.LastIndex(url, "/")+1]
	next := -1
	var buffer float64 // секунд в буфере
	playing := false
	var stallStart time.Time
	last := time.Now()
	st.active.Add(1)
	defer st.active.Add(-1)
	for ctx.Err() == nil {
		t0 := time.Now()
		req := get(ctx, url)
		resp, err := client.Do(req)
		st.reqs.Add(1)
		if err != nil {
			if ctx.Err() != nil {
				return
			}
			st.code("conn_error")
			time.Sleep(time.Second)
			continue
		}
		body, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		st.bytes.Add(int64(len(body)))
		st.mu.Lock()
		st.plMs = append(st.plMs, float64(time.Since(t0).Microseconds())/1000)
		st.mu.Unlock()
		st.code(strconv.Itoa(resp.StatusCode))
		if resp.StatusCode != 200 {
			time.Sleep(time.Second)
			continue
		}
		_, segs := parse(string(body))
		if len(segs) == 0 {
			time.Sleep(time.Second)
			continue
		}
		now := time.Now()
		for _, s := range segs {
			firstSeen.LoadOrStore(s.seq, now)
		}
		if next < 0 { // старт как у hls.js с liveSyncDuration ≈2,5 с: с предпоследнего сегмента
			next = segs[len(segs)-1].seq - 1
			if next < segs[0].seq {
				next = segs[0].seq
			}
		}
		if os.Getenv("LG_DEBUG") != "" && id == 0 {
			fmt.Fprintf(os.Stderr, "poll %.2f first=%d last=%d n=%d next=%d age=%s\n", float64(time.Now().UnixMilli()%100000)/1000, segs[0].seq, segs[len(segs)-1].seq, len(segs), next, resp.Header.Get("Age"))
		}
		wait := segs[len(segs)-1].dur
		if segs[len(segs)-1].seq < next {
			wait /= 2 // плейлист не изменился — повтор через половину длительности, как hls.js
		}
		for _, s := range segs {
			if s.seq < next {
				continue
			}
			t1 := time.Now()
			req := get(ctx, base+s.uri)
			resp, err := client.Do(req)
			st.reqs.Add(1)
			if err != nil {
				if ctx.Err() != nil {
					return
				}
				st.code("conn_error")
				break
			}
			nb, _ := io.Copy(io.Discard, resp.Body)
			resp.Body.Close()
			st.bytes.Add(nb)
			st.code(strconv.Itoa(resp.StatusCode))
			if resp.StatusCode != 200 {
				break
			}
			el := time.Since(t1)
			st.mu.Lock()
			st.segMs = append(st.segMs, float64(el.Microseconds())/1000)
			if v, ok := firstSeen.Load(s.seq); ok {
				st.ageMs = append(st.ageMs, float64(time.Since(v.(time.Time)).Microseconds())/1000)
			}
			st.mu.Unlock()
			// буфер: убывает в реальном времени, растёт на длительность сегмента
			nowb := time.Now()
			if playing {
				buffer -= nowb.Sub(last).Seconds()
				if buffer < 0 {
					st.stalls.Add(1)
					playing = false
					stallStart = nowb.Add(time.Duration(buffer * float64(time.Second)))
					buffer = 0
				}
			}
			last = nowb
			buffer += s.dur
			if os.Getenv("LG_DEBUG") != "" && id == 0 {
				fmt.Fprintf(os.Stderr, "dbg %.2f seq=%d dur=%.2f buf=%.2f playing=%v\n", float64(time.Now().UnixMilli()%100000)/1000, s.seq, s.dur, buffer, playing)
			}
			if !playing && buffer >= 2*s.dur {
				if !stallStart.IsZero() {
					st.stallMs.Add(time.Since(stallStart).Milliseconds())
					stallStart = time.Time{}
				}
				playing = true
				st.started.Add(1)
			}
			next = s.seq + 1
		}
		d := time.Duration(wait*float64(time.Second)) - time.Since(t0)
		if d < 200*time.Millisecond {
			d = 200 * time.Millisecond
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(d):
		}
	}
}

func main() {
	url := flag.String("url", "", "адрес плейлиста (index.m3u8) с подписью")
	n := flag.Int("viewers", 100, "зрителей")
	dur := flag.Duration("duration", 60*time.Second, "длительность")
	ramp := flag.Duration("ramp", 10*time.Second, "за сколько подключить всех")
	resolve := flag.String("resolve", "", "host:port:ip — как curl --resolve")
	insecure := flag.Bool("insecure", false, "не проверять сертификат (испытательный стенд)")
	every := flag.Duration("report", 10*time.Second, "промежуточный отчёт")
	header := flag.String("header", "", "дополнительный заголовок Имя: значение (ключ узла для прямого запроса к источнику)")
	flag.Parse()
	if h := strings.SplitN(*header, ":", 2); len(h) == 2 {
		hdrName, hdrValue = strings.TrimSpace(h[0]), strings.TrimSpace(h[1])
	}
	if *url == "" {
		fmt.Fprintln(os.Stderr, "нужен -url")
		os.Exit(2)
	}
	dialer := &net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}
	dial := dialer.DialContext
	if *resolve != "" {
		p := strings.SplitN(*resolve, ":", 3)
		from, to := p[0]+":"+p[1], p[2]+":"+p[1]
		dial = func(ctx context.Context, nw, addr string) (net.Conn, error) {
			if addr == from {
				addr = to
			}
			return dialer.DialContext(ctx, nw, addr)
		}
	}
	st := &stats{codes: map[string]int{}}
	ctx, cancel := context.WithTimeout(context.Background(), *dur+*ramp)
	defer cancel()
	firstSeen := &sync.Map{}
	var wg sync.WaitGroup
	start := time.Now()
	go func() {
		var lb, lr int64
		lt := time.Now()
		tk := time.NewTicker(*every)
		for {
			select {
			case <-ctx.Done():
				return
			case <-tk.C:
				b, r := st.bytes.Load(), st.reqs.Load()
				dt := time.Since(lt).Seconds()
				st.mu.Lock()
				p95 := pct(st.segMs, 0.95)
				st.mu.Unlock()
				fmt.Fprintf(os.Stderr, "t=%3.0fs viewers=%d mbit=%.0f rps=%.0f seg_p95=%.0fms stalls=%d\n", time.Since(start).Seconds(), st.active.Load(),
					float64(b-lb)*8/dt/1e6, float64(r-lr)/dt, p95, st.stalls.Load())
				lb, lr, lt = b, r, time.Now()
			}
		}
	}()
	measureFrom := time.Time{}
	var mb int64
	for i := 0; i < *n; i++ {
		tr := &http.Transport{DialContext: dial, MaxIdleConnsPerHost: 2, MaxConnsPerHost: 2, IdleConnTimeout: 60 * time.Second, ForceAttemptHTTP2: false,
			TLSClientConfig: &tls.Config{InsecureSkipVerify: *insecure}, DisableCompression: true}
		c := &http.Client{Transport: tr, Timeout: 20 * time.Second}
		wg.Add(1)
		go func(id int) { defer wg.Done(); viewer(ctx, id, *url, c, st, firstSeen) }(i)
		if *n > 1 {
			time.Sleep(time.Duration(int64(*ramp) / int64(*n)) + time.Duration(rand.Intn(1000))*time.Microsecond)
		}
	}
	// измерение — после подключения всех зрителей
	time.Sleep(2 * time.Second)
	measureFrom = time.Now()
	mb = st.bytes.Load()
	st.mu.Lock()
	st.segMs, st.plMs, st.ageMs = nil, nil, nil
	st.mu.Unlock()
	st.stalls.Store(0)
	wg.Wait()
	el := time.Since(measureFrom).Seconds()
	st.mu.Lock()
	defer st.mu.Unlock()
	out := map[string]any{
		"viewers": *n, "measured_s": int(el), "mbit_per_s": float64(st.bytes.Load()-mb) * 8 / el / 1e6,
		"seg_ms": map[string]float64{"p50": pct(st.segMs, 0.5), "p95": pct(st.segMs, 0.95), "p99": pct(st.segMs, 0.99), "max": pct(st.segMs, 1)},
		"playlist_ms": map[string]float64{"p50": pct(st.plMs, 0.5), "p95": pct(st.plMs, 0.95), "p99": pct(st.plMs, 0.99)},
		"segment_age_ms": map[string]float64{"p50": pct(st.ageMs, 0.5), "p95": pct(st.ageMs, 0.95)},
		"segments": len(st.segMs), "stalls": st.stalls.Load(), "codes": st.codes,
	}
	json.NewEncoder(os.Stdout).Encode(out)
}
