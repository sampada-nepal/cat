import argparse
import json
import csv
import time
import os
from pathlib import Path
from datetime import datetime
import shutil
import threading
import subprocess
from flask import Flask, jsonify, render_template_string

tracked_coordinates = []
data_lock = threading.Lock()

def find_database_file():
    home_dir = Path.home()
    possible_paths = [
        home_dir / "Library/Caches/com.apple.findmy.fmipcore/Items.data",
        home_dir / "Library/Application Support/com.apple.findmy/Items.data"
    ]
    for path in possible_paths:
        if path.exists():
            print(f"[*] Found data file at: {path}")
            return path
    print("[!] Error: Could not find the 'Items.data' file.")
    print("    Please ensure the Find My app has been run at least once.")
    return None

def get_data_snapshot(source_path):
    temp_json_path = Path(f"/tmp/items_{int(time.time())}.json")
    try:
        subprocess.run(
            ['plutil', '-convert', 'json', '-o', str(temp_json_path), '--', str(source_path)],
            check=True,
            capture_output=True
        )
        with open(temp_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data
    except subprocess.CalledProcessError as e:
        print(f"[!] Error converting data file with plutil: {e.stderr.decode()}")
        return None
    except Exception as e:
        print(f"[!] An unexpected error occurred while reading snapshot: {e}")
        return None
    finally:
        if temp_json_path.exists():
            os.remove(temp_json_path)

def dump_all_items(source_path, output_filename):
    print(f"[*] Dumping all items to '{output_filename}'...")
    all_items = get_data_snapshot(source_path)
    if not all_items:
        print("[!] Could not retrieve item data.")
        return
    headers = ['name', 'serialNumber', 'model', 'batteryStatus', 'latitude', 'longitude', 'timestamp', 'address', 'isOld']
    with open(output_filename, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=headers)
        writer.writeheader()
        for item in all_items:
            location = item.get('location', {})
            address = item.get('address', {})
            timestamp_unix = location.get('timeStamp', 0) / 1000
            timestamp_human = datetime.fromtimestamp(timestamp_unix).strftime('%Y-%m-%d %H:%M:%S') if timestamp_unix > 0 else 'N/A'
            full_address = f"{address.get('streetAddress', '')}, {address.get('locality', '')}, {address.get('stateCode', '')} {address.get('country', '')}".strip(', ')
            writer.writerow({'name': item.get('name', 'N/A'), 'serialNumber': item.get('serialNumber', 'N/A'), 'model': item.get('productType', {}).get('type', 'N/A'), 'batteryStatus': item.get('batteryStatus', 'N/A'), 'latitude': location.get('latitude', 'N/A'), 'longitude': location.get('longitude', 'N/A'), 'timestamp': timestamp_human, 'address': full_address, 'isOld': location.get('isOld', 'N/A')})
    print(f"[+] Success! Data for {len(all_items)} items saved.")

def track_specific_item_to_csv(source_path, airtag_name, output_filename, interval):
    print(f"[*] Starting to track '{airtag_name}'. Press Ctrl+C to stop.")
    print(f"[*] Logging data to '{output_filename}' every {interval} seconds.")
    print("-" * 30)
    if not os.path.exists(output_filename):
        with open(output_filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['timestamp', 'latitude', 'longitude', 'address'])
    while True:
        try:
            all_items = get_data_snapshot(source_path)
            if not all_items:
                print("[!] Skipping this cycle as data could not be read.")
                time.sleep(interval)
                continue
            found_item = next((item for item in all_items if item.get('name') == airtag_name), None)
            if found_item:
                location = found_item.get('location')
                if location and location.get('latitude') is not None:
                    timestamp_unix = location.get('timeStamp', 0) / 1000
                    timestamp_human = datetime.fromtimestamp(timestamp_unix).strftime('%Y-%m-%d %H:%M:%S')
                    latitude = location.get('latitude')
                    longitude = location.get('longitude')
                    address_info = found_item.get('address', {})
                    full_address = f"{address_info.get('streetAddress', '')}, {address_info.get('locality', '')}, {address_info.get('stateCode', '')} {address_info.get('country', '')}".strip(', ')
                    with open(output_filename, 'a', newline='', encoding='utf-8') as f:
                        writer = csv.writer(f)
                        writer.writerow([timestamp_human, latitude, longitude, full_address])
                    print(f"[{timestamp_human}] Location logged: Lat={latitude}, Lon={longitude}")
                else:
                    print(f"[*] Found '{airtag_name}', but it has no current location data.")
            else:
                print(f"[!] Searching... Could not find an AirTag named '{airtag_name}'.")
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\n[*] Stopping tracker. Goodbye!")
            break
        except Exception as e:
            print(f"[!] An unexpected error occurred in the main loop: {e}")
            time.sleep(interval)

def track_for_server(source_path, airtag_name, interval, log_filename):
    while True:
        try:
            all_items = get_data_snapshot(source_path)
            if not all_items:
                time.sleep(interval)
                continue
            found_item = next((item for item in all_items if item.get('name') == airtag_name), None)
            if found_item:
                location = found_item.get('location')
                if location and location.get('latitude') is not None:
                    timestamp_unix = location.get('timeStamp', 0) / 1000
                    timestamp_human = datetime.fromtimestamp(timestamp_unix).strftime('%Y-%m-%d %H:%M:%S')
                    latitude = location.get('latitude')
                    longitude = location.get('longitude')
                    new_coord = {'lat': latitude, 'lon': longitude, 'ts': timestamp_human}
                    
                    with data_lock:
                        if not tracked_coordinates or tracked_coordinates[-1]['ts'] != new_coord['ts']:
                            tracked_coordinates.append(new_coord)
                            print(f"[*] [WEB] New location logged at {timestamp_human}")
                            with open(log_filename, 'a', newline='', encoding='utf-8') as f:
                                writer = csv.writer(f)
                                writer.writerow([timestamp_human, latitude, longitude])
        except Exception as e:
            print(f"[!] An error occurred in the tracking thread: {e}")
        time.sleep(interval)

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Live Map - Tracking: {{ airtag_name }}</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        #map { height: 100vh; width: 100%; z-index: 10; }
        body { overflow: hidden; }
        #loading-overlay { z-index: 1000; }
    </style>
</head>
<body class="bg-gray-100">
    <div id="map"></div>
    <div id="loading-overlay" class="absolute inset-0 bg-gray-100 bg-opacity-80 flex flex-col items-center justify-center">
        <h1 class="text-3xl font-bold mb-2 text-gray-800">Tracking {{ airtag_name }}</h1>
        <p id="status-text" class="text-lg text-gray-600">Waiting for first location from server...</p>
    </div>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script>
        let map;
        let polyline;
        let markers = [];

        async function updateMap() {
            try {
                const response = await fetch('/api/data');
                const coordinates = await response.json();
                const statusText = document.getElementById('status-text');

                if (coordinates.length === 0) {
                    statusText.textContent = 'No location data received yet. Waiting...';
                    return;
                }
                document.getElementById('loading-overlay').style.display = 'none';

                if (!map) {
                    map = L.map('map').setView([coordinates[0].lat, coordinates[0].lon], 16);
                    L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
                        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
                        subdomains: 'abcd',
                        maxZoom: 20
                    }).addTo(map);
                    polyline = L.polyline([], { color: '#FC4C02', weight: 5 }).addTo(map);
                }
                
                markers.forEach(m => map.removeLayer(m));
                markers = [];

                const latLngs = coordinates.map(c => [c.lat, c.lon]);
                polyline.setLatLngs(latLngs);

                const endIcon = L.icon({
                    iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-red.png',
                    shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
                    iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34], shadowSize: [41, 41]
                });
                const startIcon = L.icon({
                    iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-green.png',
                    shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
                    iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34], shadowSize: [41, 41]
                });

                coordinates.forEach((coord, index) => {
                    let marker;
                    if (index === 0) {
                        marker = L.marker([coord.lat, coord.lon], { icon: startIcon })
                            .addTo(map).bindPopup(`<b>Start:</b><br>${coord.ts}`);
                    } else if (index === coordinates.length - 1) {
                        marker = L.marker([coord.lat, coord.lon], { icon: endIcon })
                            .addTo(map).bindPopup(`<b>Current:</b><br>${coord.ts}`).openPopup();
                    } else {
                        marker = L.circleMarker([coord.lat, coord.lon], {
                            radius: 5, fillColor: "#FC4C02", color: "#fff",
                            weight: 1, opacity: 1, fillOpacity: 0.8
                        }).addTo(map).bindPopup(`<b>Logged at:</b><br>${coord.ts}`);
                    }
                    markers.push(marker);
                });
                
                if (coordinates.length > 0) {
                    map.fitBounds(polyline.getBounds().pad(0.1));
                }

            } catch (error) {
                console.error("Error updating map:", error);
            }
        }
        setInterval(updateMap, 5000);
        window.onload = updateMap;
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    airtag_name = app.config.get('AIRTAG_NAME', 'Item')
    return render_template_string(HTML_TEMPLATE, airtag_name=airtag_name)

@app.route('/api/data')
def api_data():
    with data_lock:
        return jsonify(tracked_coordinates)

def main():
    parser = argparse.ArgumentParser(description="A tool to dump, track, or serve a live map for Apple Find My items.", formatter_class=argparse.RawTextHelpFormatter)
    subparsers = parser.add_subparsers(dest='command', required=True)

    parser_dump = subparsers.add_parser('dump', help="Dump all item data to a CSV once.")
    parser_dump.add_argument('-o', '--output', default='all_items.csv', help="Output CSV file name.")

    parser_track = subparsers.add_parser('track', help="Track a specific item to a CSV file.")
    parser_track.add_argument('name', help="The exact name of the AirTag to track.")
    parser_track.add_argument('-o', '--output', default='tracking_log.csv', help="Output CSV file name for the log.")
    parser_track.add_argument('-i', '--interval', type=int, default=60, help="Time interval between checks, in seconds.")

    parser_serve = subparsers.add_parser('serve', help="Serve a live web map for a specific item.")
    parser_serve.add_argument('name', help="The exact name of the AirTag to display on the map.")
    parser_serve.add_argument('-o', '--output', help="Optional: Specify a log file for persistent tracking history.")
    parser_serve.add_argument('-i', '--interval', type=int, default=30, help="Time interval for background checks, in seconds.")
    parser_serve.add_argument('--host', default='127.0.0.1', help="Host for the web server.")
    parser_serve.add_argument('--port', type=int, default=5000, help="Port for the web server.")

    args = parser.parse_args()
    
    db_path = find_database_file()
    if not db_path:
        return

    if args.command == 'dump':
        dump_all_items(db_path, args.output)
    elif args.command == 'track':
        track_specific_item_to_csv(db_path, args.name, args.output, args.interval)
    elif args.command == 'serve':
        log_filename = args.output or f"{args.name.replace(' ', '_')}_live_log.csv"
        
        if os.path.exists(log_filename):
            print(f"[*] Loading previous locations from '{log_filename}'...")
            with open(log_filename, 'r', newline='', encoding='utf-8') as f:
                reader = csv.reader(f)
                try:
                    next(reader, None)
                    for row in reader:
                        if len(row) >= 3:
                            tracked_coordinates.append({'ts': row[0], 'lat': float(row[1]), 'lon': float(row[2])})
                except (ValueError, IndexError) as e:
                    print(f"[!] Warning: Could not parse a row in {log_filename}. Error: {e}")
            print(f"[*] Loaded {len(tracked_coordinates)} previous locations.")
        else:
            with open(log_filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['timestamp', 'latitude', 'longitude'])

        print(f"[*] Tracking '{args.name}' in the background...")
        app.config['AIRTAG_NAME'] = args.name
        
        tracker_thread = threading.Thread(target=track_for_server, args=(db_path, args.name, args.interval, log_filename), daemon=True)
        tracker_thread.start()
        
        print(f"[*] Starting web server on http://{args.host}:{args.port}")
        app.run(host=args.host, port=args.port)

if __name__ == "__main__":
    main()
