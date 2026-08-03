import re
import sys
import time
import ipaddress
import subprocess
import statistics as st
import threading
from collections import defaultdict, deque

import psutil
from scapy.all import AsyncSniffer, conf, get_if_hwaddr, get_if_addr, sr1
from scapy.layers.l2 import Ether
from scapy.layers.inet import IP, UDP, ICMP

PROC_NAMES = ("polaris-win64-shipping.exe",)
PORT_REFRESH = 2.0
WINDOW = 3.0
PEER_RATE = 25
PEER_MIN_PKTS = 30
INGAME_RATE = 100
INGAME_HOLD = 10
INGAME_MIN_AGE = 4.0
BURST_MS = 6.0
TICK = 16.667
WARMUP = 2.0
PING_EVERY = 0.7
ECHO_TRIES = 2
ECHO_ENOUGH = 6
SWEEP_MAX = 24
SWEEP_TIMEOUT = 0.5
ACC_MAX = 20000
FLOW_TTL_MS = 30000
PAUSE_MS = 500.0
ACCEPT_PAUSE_MS = 60.0
ACCEPT_WARMUP = 1.0
ACCEPT_MIN_PKTS = 60
ACCEPT_MIN_GAPS = 30
SWEEP_TOL = 2
SWEEP_BUDGET = 4.0
RTT_TTL = 900.0
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
RE_MS = re.compile(r"[=<]\s*(\d+)\s*ms", re.I)
RE_HOP = re.compile(r"^\s*\d+\s+(.*)$")


def run(args, timeout):
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout,
                           creationflags=NOWIN)
    except Exception:
        return ""
    for enc in ("cp949", "utf-8", "latin-1"):
        try:
            return p.stdout.decode(enc)
        except Exception:
            continue
    return ""


def echo(ip, wait=600):
    txt = run(["ping", "-n", "1", "-w", str(wait), ip], wait / 1000.0 + 2.0)
    if "TTL" not in txt.upper():
        return None
    m = RE_MS.search(txt)
    return float(m.group(1)) if m else None


def probe_ttl(ip, ttl, tries=2):
    for _ in range(tries):
        pkt = IP(dst=ip, ttl=ttl) / ICMP()
        try:
            r = sr1(pkt, timeout=SWEEP_TIMEOUT, verbose=0)
        except Exception:
            return None
        if r is not None:
            try:
                return (float(r.time) - float(pkt.sent_time)) * 1000.0
            except Exception:
                return None
    return None


def sweep(ip):
    memo = {}
    t0 = time.time()

    def probe(n, tries=2):
        if n in memo:
            return memo[n]
        r = probe_ttl(ip, n, tries=tries)
        memo[n] = r
        return r

    lo, hi = 1, SWEEP_MAX
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if probe(mid) is not None:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1

    n = (best or 0) + 1
    miss = 0
    while n <= SWEEP_MAX and miss < SWEEP_TOL:
        if time.time() - t0 > SWEEP_BUDGET:
            break
        if probe(n, tries=1) is not None:
            best = n
            miss = 0
        else:
            miss += 1
        n += 1

    if best is None:
        return None
    vals = [memo[best]]
    for _ in range(2):
        r = probe_ttl(ip, best, tries=1)
        if r is not None:
            vals.append(r)
    return st.median(vals)


def last_hop_slow(ip, hops=18, wait=400):
    txt = run(["tracert", "-d", "-4", "-h", str(hops), "-w", str(wait), ip],
              hops * wait / 1000.0 * 3 + 10)
    best = None
    for line in txt.splitlines():
        m = RE_HOP.match(line)
        if not m:
            continue
        vals = [float(x) for x in re.findall(r"(\d+)\s*ms", m.group(1), re.I)]
        if vals:
            best = st.median(vals)
    return best


def frames_of(rtt):
    return rtt / 2.0 / TICK


def est_tick(times, warmup=ACCEPT_WARMUP):
    if len(times) < ACCEPT_MIN_PKTS:
        return None
    lo = times[0] + warmup * 1000
    t = [x for x in times if x >= lo]
    if len(t) < ACCEPT_MIN_PKTS:
        return None
    g = [t[i + 1] - t[i] for i in range(len(t) - 1)]
    base = [x for x in g if BURST_MS <= x <= 200.0]
    if len(base) < ACCEPT_MIN_GAPS:
        return None
    return st.median(base)


def stability(times, tick=TICK, warmup=WARMUP, min_pkts=25, min_gaps=10,
              est=False, pause=None):
    if len(times) < min_pkts:
        return None
    lo = times[0] + warmup * 1000
    t = [x for x in times if x >= lo]
    if len(t) < min_pkts:
        return None
    g = [t[i + 1] - t[i] for i in range(len(t) - 1)]
    lim = pause if pause else max(PAUSE_MS, tick * 10)
    fr = [x for x in g if BURST_MS <= x <= lim]
    if len(fr) < min_gaps:
        return None
    span = sum(fr) / 1000.0
    exp = miss = stall = 0
    for x in fr:
        n = max(1, round(x / tick))
        exp += n
        miss += n - 1
        if x > tick * 2.5:
            stall += 1
    dev = [abs(x - tick) for x in fr if x <= tick * 2.5]
    return {"fps": len(fr) / span if span else 0,
            "jit": st.median(dev) if dev else 0.0,
            "stall": stall,
            "spm": stall / (span / 60) if span else 0,
            "loss": 100.0 * miss / exp if exp else 0.0,
            "span": span,
            "tick": tick,
            "est": est}


def stability_accept(times):
    base = est_tick(times)
    if base is None:
        return None
    return stability(times, tick=base, warmup=ACCEPT_WARMUP,
                     min_pkts=ACCEPT_MIN_PKTS, min_gaps=ACCEPT_MIN_GAPS,
                     est=True, pause=max(ACCEPT_PAUSE_MS, base * 3.6))


def verdict(rtt, sta):
    lag = None
    if rtt and rtt["src"] in ("echo", "hop"):
        f = frames_of(rtt["med"])
        lag = "GOOD" if f < 1.0 else ("WARN" if f < 2.0 else "BAD")
    stb = None
    if sta:
        k = sta.get("tick", TICK) / TICK
        if sta.get("est"):
            bad = sta["spm"] > 45 or sta["jit"] > 4.0 * k or sta["loss"] > 3.0
            warn = sta["spm"] > 30 or sta["jit"] > 3.0 * k or sta["loss"] > 2.0
        else:
            bad = sta["spm"] > 30 or sta["jit"] > 4.0 * k or sta["loss"] > 6
            warn = sta["spm"] > 20 or sta["jit"] > 3.0 * k or sta["loss"] > 4
        stb = "BAD" if bad else ("WARN" if warn else "GOOD")
    rank = {None: -1, "GOOD": 0, "WARN": 1, "BAD": 2}
    return lag, stb, max([lag, stb], key=lambda x: rank[x])


def npcap_state():
    import ctypes
    try:
        ctypes.WinDLL("wpcap.dll")
        return "ok"
    except Exception:
        pass
    txt = run(["sc", "query", "npcap"], 5)
    if "SERVICE_NAME" in txt.upper() or "NPCAP" in txt.upper():
        return "no_compat"
    return "missing"


def npcap_ok():
    return npcap_state() == "ok"


class GamePorts(threading.Thread):
    daemon = True

    def __init__(self):
        super().__init__()
        self.ports = set()
        self.pid = None
        self.mode = "init"
        self.lock = threading.Lock()

    def find_pid(self):
        best = None
        for p in psutil.process_iter(["pid", "name", "memory_info"]):
            n = (p.info["name"] or "").lower()
            if n in PROC_NAMES:
                mem = p.info["memory_info"].rss if p.info["memory_info"] else 0
                if best is None or mem > best[1]:
                    best = (p.info["pid"], mem)
        return best

    def collect(self, pid):
        try:
            return {c.laddr.port for c in psutil.Process(pid).net_connections("udp")
                    if c.laddr}
        except Exception:
            pass
        try:
            return {c.laddr.port for c in psutil.net_connections("udp")
                    if c.pid == pid and c.laddr}
        except Exception:
            return None

    def run(self):
        while True:
            hit = self.find_pid()
            if not hit:
                with self.lock:
                    self.ports, self.pid, self.mode = set(), None, "no_game"
                time.sleep(PORT_REFRESH)
                continue
            ports = self.collect(hit[0])
            with self.lock:
                self.pid = hit[0]
                if ports is None:
                    self.ports, self.mode = set(), "no_perm"
                else:
                    self.ports, self.mode = ports, "ok"
            time.sleep(PORT_REFRESH)

    def match(self, port):
        with self.lock:
            if self.mode == "ok":
                return port in self.ports
            return self.mode == "no_perm"

    def status(self):
        with self.lock:
            return self.mode, self.pid


class Prober(threading.Thread):
    daemon = True

    def __init__(self):
        super().__init__()
        self.target = None
        self.echo = defaultdict(list)
        self.fails = defaultdict(int)
        self.hop = {}
        self.at = {}
        self.raw_ok = None
        self.lock = threading.Lock()

    def purge(self):
        now = time.time()
        with self.lock:
            old = [ip for ip, t in self.at.items() if now - t > RTT_TTL]
            for ip in old:
                if ip == self.target:
                    continue
                self.at.pop(ip, None)
                self.echo.pop(ip, None)
                self.fails.pop(ip, None)
                self.hop.pop(ip, None)

    def measure_hop(self, ip):
        if self.raw_ok is not False:
            r = sweep(ip)
            if r is not None:
                self.raw_ok = True
                return r
            if self.raw_ok is None:
                self.raw_ok = False
        return last_hop_slow(ip)

    def run(self):
        while True:
            self.purge()
            t = self.target
            if not t:
                time.sleep(0.3)
                continue
            with self.lock:
                have = len(self.echo.get(t, []))
            if have >= ECHO_ENOUGH:
                time.sleep(2.0)
                continue
            r = echo(t)
            if self.target != t:
                continue
            with self.lock:
                self.at[t] = time.time()
                if r is None:
                    self.fails[t] += 1
                else:
                    self.echo[t].append(r)
                need = (not self.echo[t] and self.fails[t] >= ECHO_TRIES
                        and t not in self.hop)
            if need and self.target == t:
                with self.lock:
                    self.hop[t] = None
                v = self.measure_hop(t)
                with self.lock:
                    self.hop[t] = v
                    self.at[t] = time.time()
            time.sleep(PING_EVERY)

    def stats(self, ip):
        with self.lock:
            s = list(self.echo.get(ip, []))
            f = self.fails.get(ip, 0)
            h = self.hop.get(ip, "none")
        if s:
            return {"src": "echo", "med": st.median(s), "min": min(s),
                    "max": max(s), "n": len(s), "fails": f}
        if h not in ("none", None):
            return {"src": "hop", "med": h, "min": h, "max": h, "n": 1, "fails": f}
        if h is None:
            return {"src": "dead", "fails": f}
        if f:
            return {"src": "trying", "fails": f}
        return None


class Flow:
    def __init__(self):
        self.win = deque()
        self.acc = deque(maxlen=ACC_MAX)
        self.tx = 0
        self.rx = 0
        self.first = None
        self.last = None

    def add(self, ms, out):
        if out:
            self.tx += 1
        else:
            self.rx += 1
            self.win.append(ms)
            self.acc.append(ms)
        if self.first is None:
            self.first = ms
        self.last = ms

    def trim(self):
        if not self.win:
            return
        cut = self.win[-1] - WINDOW * 1000
        while self.win and self.win[0] < cut:
            self.win.popleft()

    def rate(self):
        if len(self.win) < PEER_MIN_PKTS:
            return 0.0
        return len(self.win) / max((self.win[-1] - self.win[0]) / 1000.0, 1.0)

    def reset_acc(self):
        self.acc = deque(self.win, maxlen=ACC_MAX)


class Engine(threading.Thread):
    daemon = True

    def __init__(self):
        super().__init__()
        self.gp = GamePorts()
        self.prober = Prober()
        self.flows = defaultdict(Flow)
        self.flock = threading.Lock()
        self.active = None
        self.phase = "idle"
        self.hot = 0
        self.lock = threading.Lock()
        self.state = {"phase": "idle", "game": "init",
                      "connected": False,
                      "rtt": None, "sta": None, "lag": None, "stb": None,
                      "all": None, "held": 0.0, "rate": 0.0}
        self.mac = None
        self.sniffer = None
        self.error = None

    def start_all(self):
        self.gp.start()
        self.prober.start()
        try:
            iface = conf.route.route("8.8.8.8")[0]
        except Exception:
            iface = conf.iface
        try:
            self.mac = get_if_hwaddr(iface).lower()
            self.local_ip = get_if_addr(iface)
        except Exception as e:
            self.error = f"어댑터 오류: {e}"
            return
        try:
            self.sniffer = AsyncSniffer(
                iface=iface,
                filter="udp and not broadcast and not multicast",
                prn=self.handle, store=False)
            self.sniffer.start()
        except Exception as e:
            self.error = f"캡처를 시작할 수 없습니다.<br><br>{e}"
            return
        time.sleep(0.8)
        if not getattr(self.sniffer, "running", False):
            self.error = ("캡처가 시작되지 않았습니다.<br><br>"
                          "Npcap이 올바르게 설치되지 않았을 수 있습니다.")
            return
        self.start()

    def handle(self, p):
        if Ether not in p or IP not in p or UDP not in p:
            return
        out = p[Ether].src.lower() == self.mac
        local = p[UDP].sport if out else p[UDP].dport
        if not self.gp.match(local):
            return
        remote = p[IP].dst if out else p[IP].src
        try:
            ip = ipaddress.ip_address(remote)
        except ValueError:
            return
        if ip.is_private or ip.is_multicast or ip.is_reserved or ip.is_loopback:
            return
        port = p[UDP].dport if out else p[UDP].sport
        with self.flock:
            self.flows[(remote, port)].add(float(p.time) * 1000.0, out)

    def pick(self):
        now = time.time() * 1000.0
        best = None
        with self.flock:
            dead = [k for k, f in self.flows.items()
                    if f.last and now - f.last > FLOW_TTL_MS]
            for k in dead:
                del self.flows[k]
            for key, f in self.flows.items():
                f.trim()
                if f.last and now - f.last > 2000:
                    continue
                r = f.rate()
                if r > PEER_RATE and (best is None or r > best[1]):
                    best = (key, r)
        return best

    def step(self):
        best = self.pick()
        key = best[0] if best else None
        rate = best[1] if best else 0.0

        if key != self.active:
            self.active = key
            self.phase = "accept" if key else "idle"
            self.hot = 0
            self.prober.target = key[0] if key else None
            if key:
                with self.flock:
                    self.flows[key].reset_acc()

        acc = None
        first = last = None
        if key:
            with self.flock:
                f = self.flows[key]
                acc = list(f.acc)
                first, last = f.first, f.last

        if key and self.phase == "accept":
            age = (last - first) / 1000.0
            if age > INGAME_MIN_AGE and rate > INGAME_RATE:
                self.hot += 1
            else:
                self.hot = 0
            if self.hot >= INGAME_HOLD:
                self.phase = "ingame"
                with self.flock:
                    self.flows[key].reset_acc()
                    acc = list(self.flows[key].acc)
                print(f"[대전 시작 감지] rate={rate:.0f}/s age={age:.1f}s")

        gmode, _ = self.gp.status()
        if key:
            rtt = self.prober.stats(key[0])
            if self.phase == "ingame":
                sta = stability(acc)
            else:
                sta = stability_accept(acc)
            lag, stb, allv = verdict(rtt, sta)
            held = (last - first) / 1000.0
            s = {"phase": self.phase, "game": gmode, "connected": True,
                 "rtt": rtt, "sta": sta, "lag": lag, "stb": stb,
                 "all": allv, "held": held, "rate": rate}
        else:
            s = {"phase": "idle", "game": gmode,
                 "connected": False,
                 "rtt": None, "sta": None, "lag": None, "stb": None,
                 "all": None, "held": 0.0, "rate": 0.0}
        with self.lock:
            self.state = s

    def run(self):
        while True:
            try:
                self.step()
            except Exception as e:
                print(f"[engine 오류] {e!r}")
            time.sleep(0.3)

    def snapshot(self):
        with self.lock:
            return dict(self.state)
