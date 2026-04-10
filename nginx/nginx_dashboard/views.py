import os
import csv
import requests as http_requests
from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")


def _prom_query(expr):
    try:
        r = http_requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": expr},
            timeout=5
        )
        return r.json().get("data", {}).get("result", [])
    except Exception:
        return []


def _scalar(results):
    if results:
        return float(results[0]["value"][1])
    return 0


def _to_dict(results, label_key):
    return {
        r["metric"].get(label_key, "unknown"): float(r["value"][1])
        for r in results
    }


def index(request):
    return render(request, "nginx_dashboard/index.html")


def api_metrics(request):
    status_filter = request.GET.get("status", "")
    ip_filter = request.GET.get("ip", "")

    status_expr = f'nginx_requests_by_status{{status="{status_filter}"}}' if status_filter else "nginx_requests_by_status"
    ip_expr = f'nginx_requests_by_ip{{ip="{ip_filter}"}}' if ip_filter else "nginx_requests_by_ip"

    data = {
        "totals": {
            "requests":      int(_scalar(_prom_query("nginx_requests_total"))),
            "unique_ips":    int(_scalar(_prom_query("nginx_unique_ips_total"))),
            "bytes":         int(_scalar(_prom_query("nginx_bytes_total"))),
            "anomalous_ips": int(_scalar(_prom_query("nginx_anomalous_ips_total"))),
            "spikes":        int(_scalar(_prom_query("nginx_request_spike_total"))),
            "error_bursts":  int(_scalar(_prom_query("nginx_error_burst_total"))),
        },
        "by_status":     _to_dict(_prom_query(status_expr), "status"),
        "by_method":     _to_dict(_prom_query("nginx_requests_by_method"), "method"),
        "top_ips":       _to_dict(_prom_query(ip_expr), "ip"),
        "anomalous_ips": _to_dict(_prom_query("nginx_anomalous_ip_requests"), "ip"),
    }
    return JsonResponse(data)


def api_geoip(request):
    results = _prom_query("nginx_requests_by_ip")
    ips = [r["metric"].get("ip") for r in results if r["metric"].get("ip")]

    if not ips:
        return JsonResponse({"locations": []})

    try:
        r = http_requests.post(
            "http://ip-api.com/batch",
            json=[{"query": ip, "fields": "status,query,lat,lon,city,country,isp"} for ip in ips],
            timeout=10
        )
        locations = [
            {
                "ip":      item["query"],
                "lat":     item["lat"],
                "lon":     item["lon"],
                "city":    item.get("city", ""),
                "country": item.get("country", ""),
                "isp":     item.get("isp", ""),
            }
            for item in r.json()
            if item.get("status") == "success"
        ]
        return JsonResponse({"locations": locations})
    except Exception as e:
        return JsonResponse({"locations": [], "error": str(e)})


def export_csv(request):
    status_data  = _to_dict(_prom_query("nginx_requests_by_status"), "status")
    ip_data      = _to_dict(_prom_query("nginx_requests_by_ip"), "ip")
    anomaly_data = _to_dict(_prom_query("nginx_anomalous_ip_requests"), "ip")
    totals = {
        "Total Requests":   int(_scalar(_prom_query("nginx_requests_total"))),
        "Unique IPs":       int(_scalar(_prom_query("nginx_unique_ips_total"))),
        "Bytes Transferred": int(_scalar(_prom_query("nginx_bytes_total"))),
        "Anomalous IPs":    int(_scalar(_prom_query("nginx_anomalous_ips_total"))),
        "Request Spikes":   int(_scalar(_prom_query("nginx_request_spike_total"))),
        "Error Bursts":     int(_scalar(_prom_query("nginx_error_burst_total"))),
    }

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="nginx_report.csv"'
    writer = csv.writer(response)

    writer.writerow(["NGINX OBSERVABILITY REPORT"])
    writer.writerow([])
    writer.writerow(["--- SUMMARY ---"])
    for k, v in totals.items():
        writer.writerow([k, v])

    writer.writerow([])
    writer.writerow(["--- REQUESTS BY STATUS CODE ---"])
    writer.writerow(["Status", "Count"])
    for status, count in sorted(status_data.items()):
        writer.writerow([status, int(count)])

    writer.writerow([])
    writer.writerow(["--- TOP IPs ---"])
    writer.writerow(["IP Address", "Requests"])
    for ip, count in sorted(ip_data.items(), key=lambda x: x[1], reverse=True):
        writer.writerow([ip, int(count)])

    writer.writerow([])
    writer.writerow(["--- ANOMALOUS IPs (HIGH VOLUME) ---"])
    writer.writerow(["IP Address", "Requests", "Flag"])
    for ip, count in sorted(anomaly_data.items(), key=lambda x: x[1], reverse=True):
        writer.writerow([ip, int(count), "HIGH VOLUME"])

    return response
