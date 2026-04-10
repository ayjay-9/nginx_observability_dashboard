import re
import time
import os
from collections import defaultdict
from prometheus_client import start_http_server, REGISTRY
from prometheus_client.core import GaugeMetricFamily

LOG_PATH = os.getenv("NGINX_LOG_PATH", "/var/log/nginx/access.log")
PORT = int(os.getenv("EXPORTER_PORT", "9113"))

PATTERN = re.compile(
    r'(?P<clientIP>\S+) - - \[(?P<timestamp>[^\]]+)\] '
    r'"(?P<method>[A-Z]+) (?P<path>\S+) [^"]+" '
    r'(?P<status>\d+) (?P<bytes>\d+)'
)


class NginxLogCollector:
    def collect(self):
        status_counts = defaultdict(int)
        method_counts = defaultdict(int)
        ip_counts = defaultdict(int)
        total_bytes = 0
        total_requests = 0

        try:
            with open(LOG_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    m = PATTERN.match(line)
                    if not m:
                        continue
                    total_requests += 1
                    status_counts[m.group("status")] += 1
                    method_counts[m.group("method")] += 1
                    ip_counts[m.group("clientIP")] += 1
                    total_bytes += int(m.group("bytes"))
        except FileNotFoundError:
            pass

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

        # Top 10 IPs
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
        g_total.add_metric([], total_requests)
        yield g_total

        # Total bytes
        g_bytes = GaugeMetricFamily("nginx_bytes_total", "Total bytes transferred by nginx")
        g_bytes.add_metric([], total_bytes)
        yield g_bytes

        # Unique IPs
        g_ips = GaugeMetricFamily("nginx_unique_ips_total", "Total unique client IPs seen in log")
        g_ips.add_metric([], len(ip_counts))
        yield g_ips


if __name__ == "__main__":
    REGISTRY.register(NginxLogCollector())
    start_http_server(PORT)
    print(f"Nginx log exporter running on :{PORT} — reading {LOG_PATH}")
    while True:
        time.sleep(60)
