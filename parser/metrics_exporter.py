import re
import time
import os
import statistics
from collections import defaultdict
from datetime import datetime
from prometheus_client import start_http_server, REGISTRY
from prometheus_client.core import GaugeMetricFamily

LOG_PATH = os.getenv("NGINX_LOG_PATH", "/var/log/nginx/access.log")
PORT = int(os.getenv("EXPORTER_PORT", "9113"))

PATTERN = re.compile(
    r'(?P<clientIP>\S+) - - \[(?P<timestamp>[^\]]+)\] '
    r'"(?P<method>[A-Z]+) (?P<path>\S+) [^"]+" '
    r'(?P<status>\d+) (?P<bytes>\d+)'
)


def parse_log():
    ip_counts = defaultdict(int)
    status_counts = defaultdict(int)
    method_counts = defaultdict(int)
    total_bytes = 0
    total_requests = 0
    minute_requests = defaultdict(int)
    minute_errors = defaultdict(int)

    try:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                m = PATTERN.match(line)
                if not m:
                    continue
                total_requests += 1
                ip = m.group("clientIP")
                status = m.group("status")
                method = m.group("method")

                ip_counts[ip] += 1
                status_counts[status] += 1
                method_counts[method] += 1
                total_bytes += int(m.group("bytes"))

                ts_str = m.group("timestamp").split(" ")[0]
                try:
                    dt = datetime.strptime(ts_str, "%d/%b/%Y:%H:%M:%S")
                    minute_key = dt.strftime("%Y-%m-%d %H:%M")
                    minute_requests[minute_key] += 1
                    if status.startswith("4") or status.startswith("5"):
                        minute_errors[minute_key] += 1
                except ValueError:
                    pass
    except FileNotFoundError:
        pass

    return {
        "ip_counts": ip_counts,
        "status_counts": status_counts,
        "method_counts": method_counts,
        "total_bytes": total_bytes,
        "total_requests": total_requests,
        "minute_requests": minute_requests,
        "minute_errors": minute_errors,
    }


def detect_anomalies(data):
    ip_counts = data["ip_counts"]
    minute_requests = data["minute_requests"]
    minute_errors = data["minute_errors"]

    # --- IP anomalies: requests > mean + 2 * stdev ---
    anomalous_ips = {}
    if len(ip_counts) >= 2:
        counts = list(ip_counts.values())
        mean = statistics.mean(counts)
        stdev = statistics.stdev(counts)
        threshold = mean + 2 * stdev
        for ip, count in ip_counts.items():
            if count > threshold:
                anomalous_ips[ip] = count

    # --- Request spike: minutes with volume > mean + 2 * stdev ---
    spike_minutes = {}
    if len(minute_requests) >= 2:
        req_vals = list(minute_requests.values())
        mean_req = statistics.mean(req_vals)
        stdev_req = statistics.stdev(req_vals)
        threshold_req = mean_req + 2 * stdev_req
        for minute, count in minute_requests.items():
            if count > threshold_req:
                spike_minutes[minute] = count

    # --- Error bursts: minutes where error rate > 50% with >= 5 requests ---
    error_burst_minutes = {}
    for minute, total in minute_requests.items():
        errors = minute_errors.get(minute, 0)
        if total >= 5 and (errors / total) > 0.5:
            error_burst_minutes[minute] = round(errors / total * 100, 1)

    return anomalous_ips, spike_minutes, error_burst_minutes


class NginxLogCollector:
    def collect(self):
        data = parse_log()
        ip_counts = data["ip_counts"]
        status_counts = data["status_counts"]
        method_counts = data["method_counts"]
        anomalous_ips, spike_minutes, error_burst_minutes = detect_anomalies(data)

        # Requests by status code
        g_status = GaugeMetricFamily(
            "nginx_requests_by_status",
            "Nginx requests grouped by HTTP status code",
            labels=["status"]
        )
        for status, count in status_counts.items():
            g_status.add_metric([status], count)
        yield g_status

        # Requests by HTTP method
        g_method = GaugeMetricFamily(
            "nginx_requests_by_method",
            "Nginx requests grouped by HTTP method",
            labels=["method"]
        )
        for method, count in method_counts.items():
            g_method.add_metric([method], count)
        yield g_method

        # Top 10 IPs by request count
        g_ip = GaugeMetricFamily(
            "nginx_requests_by_ip",
            "Nginx requests grouped by client IP (top 10)",
            labels=["ip"]
        )
        for ip, count in sorted(ip_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
            g_ip.add_metric([ip], count)
        yield g_ip

        # Total requests
        g_total = GaugeMetricFamily("nginx_requests_total", "Total nginx requests parsed from log")
        g_total.add_metric([], data["total_requests"])
        yield g_total

        # Total bytes
        g_bytes = GaugeMetricFamily("nginx_bytes_total", "Total bytes transferred by nginx")
        g_bytes.add_metric([], data["total_bytes"])
        yield g_bytes

        # Unique IPs
        g_unique = GaugeMetricFamily("nginx_unique_ips_total", "Total unique client IPs seen in log")
        g_unique.add_metric([], len(ip_counts))
        yield g_unique

        # --- Anomaly metrics ---

        # Anomalous IPs (request count well above average)
        g_anom_ip = GaugeMetricFamily(
            "nginx_anomalous_ip_requests",
            "IPs with request count > mean + 2*stdev (anomalous volume)",
            labels=["ip"]
        )
        for ip, count in anomalous_ips.items():
            g_anom_ip.add_metric([ip], count)
        yield g_anom_ip

        # Total anomalous IP count
        g_anom_count = GaugeMetricFamily(
            "nginx_anomalous_ips_total",
            "Number of IPs flagged as anomalous"
        )
        g_anom_count.add_metric([], len(anomalous_ips))
        yield g_anom_count

        # Request spike minutes
        g_spikes = GaugeMetricFamily(
            "nginx_request_spike_total",
            "Number of 1-minute windows with request volume spike"
        )
        g_spikes.add_metric([], len(spike_minutes))
        yield g_spikes

        # Error burst minutes (error rate > 50%)
        g_error_bursts = GaugeMetricFamily(
            "nginx_error_burst_total",
            "Number of 1-minute windows with error rate above 50%"
        )
        g_error_bursts.add_metric([], len(error_burst_minutes))
        yield g_error_bursts

        # Error burst details per minute
        g_error_rate = GaugeMetricFamily(
            "nginx_error_burst_rate",
            "Error rate (%) in anomalous 1-minute windows",
            labels=["minute"]
        )
        for minute, rate in error_burst_minutes.items():
            g_error_rate.add_metric([minute], rate)
        yield g_error_rate


if __name__ == "__main__":
    REGISTRY.register(NginxLogCollector())
    start_http_server(PORT)
    print(f"Nginx log exporter running on :{PORT} — reading {LOG_PATH}")
    while True:
        time.sleep(60)
