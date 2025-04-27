import subprocess
import sys
import os
import argparse
import math  # For tile calculations
import collections  # For moving average deque
from flask import Flask, render_template, request, send_file, jsonify
from flask_socketio import SocketIO, emit
import mercantile
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import zipfile
import random
import shutil
import re
import time
import json
from shapely.geometry import Polygon, box
from shapely.ops import unary_union
import threading
from PIL import Image
from pathlib import Path

# Base directory for caching tiles, absolute path relative to script location
BASE_DIR = Path(__file__).parent.parent  # Root of map-tile-downloader
CACHE_DIR = BASE_DIR / 'tile-cache' ## Note: Fixed referece location for cached tiles
DOWNLOADS_DIR = BASE_DIR / 'downloads'
CACHE_DIR.mkdir(exist_ok=True)
DOWNLOADS_DIR.mkdir(exist_ok=True)

## Note: Moved to 'utils/dependency_installer.py'
# Ensure dependencies are installed
# def install_dependencies():
#    try:
#        with open('requirements.txt', 'r') as f:
#            requirements = f.read().splitlines()
#        subprocess.check_call([sys.executable, '-m', 'pip', 'install', *requirements])
#    except subprocess.CalledProcessError as e:
#        print(f"Failed to install dependencies: {e}")
#        sys.exit(1)
#
## Install dependencies on startup if not already installed
# install_dependencies()

app = Flask(__name__, template_folder='../templates')
socketio = SocketIO(app)


# Load map sources from config file
CONFIG_DIR = Path('config')
MAP_SOURCES_FILE = CONFIG_DIR / 'map_sources.json'
MAP_SOURCES = {}
if MAP_SOURCES_FILE.exists():
    with open(MAP_SOURCES_FILE, 'r') as f:
        MAP_SOURCES = json.load(f)
else:
    print("Warning: map_sources.json not found. No map sources available.")
    sys.exit(1)

# Global event for cancellation
download_event = threading.Event()

def sanitize_style_name(style_name):
    """Convert map style name to a filesystem-safe directory name."""
    style_name = re.sub(r'\s+', '-', style_name)  # Replace spaces with hyphens
    style_name = re.sub(r'[^a-zA-Z0-9-_]', '', style_name)  # Remove non-alphanumeric except hyphens and underscores
    return style_name

def get_style_cache_dir(style_name):
    """Get the cache directory path for a given map style name."""
    sanitized_name = sanitize_style_name(style_name)
    return CACHE_DIR / sanitized_name

def download_tile(tile, map_style, style_cache_dir, convert_to_8bit, max_retries=3):
    """Download a single tile with retries if not cancelled and not in cache, converting to 8-bit if specified."""
    if not download_event.is_set():
        return None
    tile_dir = style_cache_dir / str(tile.z) / str(tile.x)
    tile_path = tile_dir / f"{tile.y}.png"
    if tile_path.exists():
        bounds = mercantile.bounds(tile)
        socketio.emit('tile_skipped', {
            'west': bounds.west,
            'south': bounds.south,
            'east': bounds.east,
            'north': bounds.north
        })
        return tile_path
    subdomain = random.choice(['a', 'b', 'c']) if '{s}' in map_style else ''
    url = map_style.replace('{s}', subdomain).replace('{z}', str(tile.z)).replace('{x}', str(tile.x)).replace('{y}', str(tile.y))
    headers = {'User-Agent': 'MapTileDownloader/1.0'}
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                tile_dir.mkdir(parents=True, exist_ok=True)
                with open(tile_path, 'wb') as f:
                    f.write(response.content)
                if convert_to_8bit:
                    with Image.open(tile_path) as img:
                        if img.mode != 'P':  # Only convert if not already 8-bit palette
                            img = img.quantize(colors=256)
                            img.save(tile_path)
                bounds = mercantile.bounds(tile)
                socketio.emit('tile_downloaded', {
                    'west': bounds.west,
                    'south': bounds.south,
                    'east': bounds.east,
                    'north': bounds.north
                })
                return tile_path
            else:
                time.sleep(2 ** attempt)  # Exponential backoff
        except requests.RequestException:
            time.sleep(2 ** attempt)  # Exponential backoff
    socketio.emit('tile_failed', {
        'tile': f"{tile.z}/{tile.x}/{tile.y}"
    })
    return None

def get_world_tiles():
    """Generate list of tiles for zoom levels 0 to 7 for the entire world."""
    tiles = []
    for z in range(8):  # 0 to 7 inclusive
        for x in range(2**z):
            for y in range(2**z):
                tiles.append(mercantile.Tile(x, y, z))
    return tiles

def get_tiles_for_polygons(polygons_data, min_zoom, max_zoom):
    """Generate list of tiles that intersect with the given polygons for the specified zoom range."""
    polygons = [Polygon([(lng, lat) for lat, lng in poly]) for poly in polygons_data]
    overall_polygon = unary_union(polygons)
    west, south, east, north = overall_polygon.bounds
    all_tiles = []
    for z in range(min_zoom, max_zoom + 1):
        tiles = mercantile.tiles(west, south, east, north, zooms=[z])
        for tile in tiles:
            tile_bbox = mercantile.bounds(tile)
            tile_box = box(tile_bbox.west, tile_bbox.south, tile_bbox.east, tile_bbox.north)
            if any(tile_box.intersects(poly) for poly in polygons):
                all_tiles.append(tile)
    all_tiles.sort(key=lambda tile: (tile.z, -tile.x, tile.y))
    return all_tiles

def download_tiles_with_retries(tiles, map_style, style_cache_dir, convert_to_8bit):
    """Download tiles with efficient retries using parallelism and adaptive backoff."""
    socketio.emit('download_started', {'total_tiles': len(tiles)})
    retry_queue = []
    max_workers = 5
    batch_size = 10

    def process_batch(batch):
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(download_tile, tile, map_style, style_cache_dir, convert_to_8bit): tile for tile in batch}
            for future in as_completed(futures):
                if future.result() is None and download_event.is_set():
                    retry_queue.append(futures[future])

    while tiles and download_event.is_set():
        for i in range(0, len(tiles), batch_size):
            if not download_event.is_set():
                break
            batch = tiles[i:i + batch_size]
            process_batch(batch)
        tiles = retry_queue if retry_queue else []
        retry_queue = []
        if tiles:
            delay = min(2 ** len(retry_queue), 8)
            time.sleep(delay)

    if download_event.is_set():
        socketio.emit('tiles_downloaded')

def create_zip(style_cache_dir, style_name):
    """Create a zip file from the style-specific cache directory in the downloads folder."""
    sanitized_name = sanitize_style_name(style_name)
    zip_path = DOWNLOADS_DIR / f'{sanitized_name}.zip'  # Absolute path
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zipf:
        for root, _, files in os.walk(style_cache_dir):
            for file in files:
                file_path = Path(root) / file
                arcname = file_path.relative_to(style_cache_dir)
                zipf.write(file_path, arcname)
    return str(zip_path)  # Return as string for send_file

@app.route('/')
def index():
    """Render the main page."""
    return render_template('index.html')

@app.route('/get_map_sources')
def get_map_sources():
    """Return the list of map sources from the config file."""
    return jsonify(MAP_SOURCES)

@socketio.on('start_download')
def handle_start_download(data):
    """Handle download request for tiles within polygons."""
    try:
        polygons_data = data['polygons']
        min_zoom = data['min_zoom']
        max_zoom = data['max_zoom']
        map_style_url = data['map_style']
        convert_to_8bit = data.get('convert_to_8bit', False)
        style_name = next(name for name, url in MAP_SOURCES.items() if url == map_style_url)
        style_cache_dir = get_style_cache_dir(style_name)
        if min_zoom < 0 or max_zoom > 19 or min_zoom > max_zoom:
            emit('error', {'message': 'Invalid zoom range (must be 0-19, min <= max)'})
            return
        if not polygons_data:
            emit('error', {'message': 'No polygons provided'})
            return
        tiles = get_tiles_for_polygons(polygons_data, min_zoom, max_zoom)
        download_event.set()
        download_tiles_with_retries(tiles, map_style_url, style_cache_dir, convert_to_8bit)
        if download_event.is_set():
            zip_path = create_zip(style_cache_dir, style_name)
            emit('download_complete', {'zip_url': f'/download_zip?path={zip_path}'})
    except Exception as e:
        print(f"Error processing download: {e}")
        emit('error', {'message': 'An error occurred while processing your request'})

@socketio.on('start_world_download')
def handle_start_world_download(data):
    """Handle download request for world basemap tiles (zoom 0-7)."""
    try:
        map_style_url = data['map_style']
        convert_to_8bit = data.get('convert_to_8bit', False)
        style_name = next(name for name, url in MAP_SOURCES.items() if url == map_style_url)
        style_cache_dir = get_style_cache_dir(style_name)
        tiles = get_world_tiles()
        download_event.set()
        download_tiles_with_retries(tiles, map_style_url, style_cache_dir, convert_to_8bit)
        if download_event.is_set():
            zip_path = create_zip(style_cache_dir, style_name)
            emit('download_complete', {'zip_url': f'/download_zip?path={zip_path}'})
    except Exception as e:
        print(f"Error processing world download: {e}")
        emit('error', {'message': 'An error occurred while processing your request'})

@socketio.on('cancel_download')
def handle_cancel_download():
    """Handle cancellation of the download."""
    download_event.clear()
    emit('download_cancelled')

@app.route('/download_zip')
def download_zip():
    """Send the zip file to the user."""
    zip_path = request.args.get('path')
    while not Path(zip_path).exists():  # Wait until the file is created
        time.sleep(0.5)
    return send_file(zip_path, as_attachment=True, download_name=Path(zip_path).name)

@app.route('/tiles/<style_name>/<int:z>/<int:x>/<int:y>.png')
def serve_tile(style_name, z, x, y):
    """Serve a cached tile if it exists."""
    style_cache_dir = get_style_cache_dir(style_name)
    tile_path = style_cache_dir / str(z) / str(x) / f"{y}.png"
    if tile_path.exists():
        return send_file(tile_path)
    return '', 404

@app.route('/delete_cache/<style_name>', methods=['DELETE'])
def delete_cache(style_name):
    """Delete the cache directory for a specific style."""
    cache_dir = get_style_cache_dir(style_name)
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
        return '', 204
    return 'Cache not found', 404

@app.route('/get_cached_tiles/<style_name>')
def get_cached_tiles_route(style_name):
    """Return a list of [z, x, y] for cached tiles of the given style."""
    style_cache_dir = get_style_cache_dir(style_name)
    if not style_cache_dir.exists():
        return jsonify([])
    cached_tiles = []
    for z_dir in style_cache_dir.iterdir():
        if z_dir.is_dir():
            try:
                z = int(z_dir.name)
                for x_dir in z_dir.iterdir():
                    if x_dir.is_dir():
                        try:
                            x = int(x_dir.name)
                            for y_file in x_dir.glob('*.png'):
                                try:
                                    y = int(y_file.stem)
                                    cached_tiles.append([z, x, y])
                                except ValueError:
                                    pass
                        except ValueError:
                            pass
            except ValueError:
                pass
    return jsonify(cached_tiles)


# --- Add new function: get_tiles_for_bbox ---
def deg2num(lat_deg, lon_deg, zoom):
    lat_rad = math.radians(lat_deg)
    n = 2.0**zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return xtile, ytile


def get_tiles_for_zoom(west, south, east, north, zoom):
    """Generate list of tiles within the bounding box for a single specified zoom level."""
    all_tiles = []
    min_x, min_y = deg2num(north, west, zoom)
    max_x, max_y = deg2num(south, east, zoom)
    for x in range(min_x, max_x + 1):
        for y in range(min_y, max_y + 1):
            all_tiles.append(mercantile.Tile(x, y, zoom))
    all_tiles.sort(key=lambda tile: (-tile.x, tile.y))
    return all_tiles


# --- Add new function: download_tile_cli ---
def download_tile_cli(tile, map_style, style_cache_dir, convert_to_8bit, max_retries=3):
    """Download a single tile for CLI, with retries, converting to 8-bit if specified. Returns (tile_path, status, duration)."""
    tile_dir = style_cache_dir / str(tile.z) / str(tile.x)
    tile_path = tile_dir / f"{tile.y}.png"
    start_dl_time = time.time()

    if tile_path.exists():
        return tile_path, "skipped", 0

    subdomain = random.choice(["a", "b", "c"]) if "{s}" in map_style else ""
    url = (
        map_style.replace("{s}", subdomain)
        .replace("{z}", str(tile.z))
        .replace("{x}", str(tile.x))
        .replace("{y}", str(tile.y))
    )
    headers = {"User-Agent": "MapTileDownloaderCLI/1.0"}

    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=10)
            duration = (
                time.time() - start_dl_time
            )
            if response.status_code == 200:
                tile_dir.mkdir(parents=True, exist_ok=True)
                with open(tile_path, "wb") as f:
                    f.write(response.content)

                if convert_to_8bit:
                    try:
                        with Image.open(tile_path) as img:
                            if img.mode != "P":
                                img = img.quantize(colors=256)
                                img.save(tile_path)
                    except Exception as e:
                        print(
                            f"\nWarning: Failed to convert tile {tile.z}/{tile.x}/{tile.y} to 8-bit: {e}"
                        )

                return (
                    tile_path,
                    "downloaded",
                    duration,
                )

            elif response.status_code == 404:
                print(
                    f"\nWarning: Tile {tile.z}/{tile.x}/{tile.y} not found (404). Skipping."
                )
                return None, "skipped", duration

            else:
                print(
                    f"\nWarning: Tile {tile.z}/{tile.x}/{tile.y} failed with status {response.status_code}. Retrying ({attempt + 1}/{max_retries})..."
                )
                time.sleep(2**attempt)

        except requests.RequestException as e:
            duration = time.time() - start_dl_time  # Calculate duration on exception
            print(
                f"\nWarning: Tile {tile.z}/{tile.x}/{tile.y} request failed: {e}. Retrying ({attempt + 1}/{max_retries})..."
            )
            time.sleep(2**attempt)

    duration = (
        time.time() - start_dl_time
    )  # Calculate duration after all retries failed
    print(
        f"\nError: Failed to download tile {tile.z}/{tile.x}/{tile.y} after {max_retries} attempts."
    )
    return None, "failed", duration



def run_cli_download(args):
    """Orchestrates the tile download process based on CLI arguments, processing multiple styles/zooms in parallel."""
    print("Running in Command-Line Interface mode (Parallel).")

    download_tasks = []
    if not args.downloads:
        print("Error: At least one download task must be specified using --downloads.")
        sys.exit(1)

    for task_str in args.downloads:
        style_name = ""
        min_zoom_task = args.min_zoom
        max_zoom_task = args.max_zoom

        if ":" in task_str:
            parts = task_str.split(":", 1)
            style_name = parts[0]
            zoom_parts = parts[1].split("-")
            if len(zoom_parts) == 2:
                try:
                    min_zoom_task = int(zoom_parts[0])
                    max_zoom_task = int(zoom_parts[1])
                except ValueError:
                    print(
                        f"Error: Invalid zoom range format in '{task_str}'. Expected 'Min-Max'."
                    )
                    sys.exit(1)
            else:
                print(
                    f"Error: Invalid zoom range format in '{task_str}'. Expected 'Min-Max'."
                )
                sys.exit(1)
        else:
            style_name = task_str
            if min_zoom_task is None or max_zoom_task is None:
                print(
                    f"Error: Zoom range not specified for style '{style_name}' and no default --min-zoom/--max-zoom provided."
                )
                sys.exit(1)

        if style_name not in MAP_SOURCES:
            print(
                f"Error: Map style '{style_name}' not found in config/map_sources.json."
            )
            print(f"Available styles: {', '.join(MAP_SOURCES.keys())}")
            sys.exit(1)

        if min_zoom_task < 0 or max_zoom_task > 19 or min_zoom_task > max_zoom_task:
            print(
                f"Error: Invalid zoom range ({min_zoom_task}-{max_zoom_task}) for style '{style_name}'. Must be 0-19, min <= max."
            )
            sys.exit(1)

        task_details = {
            "style_name": style_name,
            "min_zoom": min_zoom_task,
            "max_zoom": max_zoom_task,
            "map_style_url": MAP_SOURCES[style_name],
            "style_cache_dir": get_style_cache_dir(style_name),
            "tiles_for_task": {},
        }
        task_details["style_cache_dir"].mkdir(exist_ok=True)
        download_tasks.append(task_details)

    if not args.bbox or len(args.bbox) != 4:
        print(
            "Error: Bounding box (--bbox WEST SOUTH EAST NORTH) is required and must contain 4 values."
        )
        sys.exit(1)
    west, south, east, north = args.bbox

    print("Calculating tiles and preparing download jobs...")
    all_tile_jobs = []
    total_tiles_across_all_tasks = 0
    for task in download_tasks:
        task_tile_count = 0
        print(
            f"  Task: Style='{task['style_name']}', Zoom={task['min_zoom']}-{task['max_zoom']}"
        )
        for z in range(task["min_zoom"], task["max_zoom"] + 1):
            tiles_for_this_zoom = get_tiles_for_zoom(west, south, east, north, z)
            count = len(tiles_for_this_zoom)
            if count > 0:
                task_tile_count += count
                print(f"    Zoom {z}: {count} tiles")
                for tile in tiles_for_this_zoom:
                    all_tile_jobs.append(
                        {
                            "tile": tile,
                            "map_style_url": task["map_style_url"],
                            "style_cache_dir": task["style_cache_dir"],
                            "convert_8bit": args.convert_8bit,
                        }
                    )
            else:
                print(f"    Zoom {z}: 0 tiles")
        print(f"  Subtotal for task: {task_tile_count} tiles")
        total_tiles_across_all_tasks += task_tile_count

    if total_tiles_across_all_tasks == 0:
        print("\nNo tiles found for any specified task.")
        sys.exit(0)

    print(f"\nGrand total tiles to process: {total_tiles_across_all_tasks}")
    print("-" * 40)

    overall_processed = 0
    overall_downloaded = 0
    overall_skipped = 0
    overall_failed = 0
    overall_start_time = time.time()
    overall_recent_download_times = collections.deque(maxlen=100)
    max_workers = 10

    print(f"Starting parallel download with {max_workers} workers...")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                download_tile_cli,
                job["tile"],
                job["map_style_url"],
                job["style_cache_dir"],
                job["convert_8bit"],
            ): job
            for job in all_tile_jobs
        }

        for future in as_completed(futures):
            job_details = futures[future]
            try:
                tile_path, status, duration = future.result()
                overall_processed += 1

                if status == "downloaded":
                    overall_downloaded += 1
                    overall_recent_download_times.append(duration)
                elif status == "skipped":
                    overall_skipped += 1
                elif status == "failed":
                    overall_failed += 1

                elapsed_time = time.time() - overall_start_time
                progress_percent = (
                    (overall_processed / total_tiles_across_all_tasks) * 100
                    if total_tiles_across_all_tasks > 0
                    else 0
                )
                eta_str = "Calculating..."

                if len(overall_recent_download_times) > 0:
                    if (
                        len(overall_recent_download_times)
                        >= overall_recent_download_times.maxlen / 2
                    ):
                        avg_time_per_tile = sum(overall_recent_download_times) / len(
                            overall_recent_download_times
                        )
                    elif overall_processed > 0 and elapsed_time > 0:
                        effective_processed_for_time = (
                            overall_downloaded + overall_failed
                        )
                        if effective_processed_for_time > 0:
                            avg_time_per_tile = (
                                elapsed_time / effective_processed_for_time
                            )
                        else:
                            avg_time_per_tile = 0
                    else:
                        avg_time_per_tile = 0

                    if avg_time_per_tile > 0:
                        remaining_tiles = (
                            total_tiles_across_all_tasks - overall_processed
                        )
                        remaining_time = avg_time_per_tile * remaining_tiles
                        eta_total_seconds = int(remaining_time)
                        eta_days = eta_total_seconds // (24 * 3600)
                        eta_hours = (eta_total_seconds % (24 * 3600)) // 3600
                        eta_minutes = (eta_total_seconds % 3600) // 60
                        eta_seconds = eta_total_seconds % 60
                        if eta_days > 0:
                            eta_str = f"{eta_days}d {eta_hours}h {eta_minutes}m {eta_seconds}s"
                        elif eta_hours > 0:
                            eta_str = f"{eta_hours}h {eta_minutes}m {eta_seconds}s"
                        else:
                            eta_str = f"{eta_minutes}m {eta_seconds}s"
                    else:
                        eta_str = "0m 0s"

                print(
                    f"\rOverall Progress: {progress_percent:.1f}% [{overall_processed}/{total_tiles_across_all_tasks}] | ETA: {eta_str}   ",
                    end="",
                )

            except Exception as exc:
                failed_tile = job_details["tile"]
                print(
                    f"\nError processing tile {failed_tile.z}/{failed_tile.x}/{failed_tile.y}: {exc}"
                )
                overall_processed += 1
                overall_failed += 1

    print()
    print("\n--- Overall Download Summary ---")
    print(f"Total tiles processed: {overall_processed}")
    print(f"Successfully downloaded: {overall_downloaded}")
    print(f"Skipped (already cached or 404): {overall_skipped}")
    print(f"Failed: {overall_failed}")
    print("-" * 40)

    if overall_failed > 0:
        print("Download process completed with errors.")

    print("\n--- Creating Zip Files ---")
    zip_success_count = 0
    for task in download_tasks:
        style_name_zip = task["style_name"]
        style_cache_dir_zip = task["style_cache_dir"]
        min_zoom_zip = task["min_zoom"]
        max_zoom_zip = task["max_zoom"]
        output_filename = (
            f"{sanitize_style_name(style_name_zip)}_{min_zoom_zip}-{max_zoom_zip}.zip"
        )
        output_path = DOWNLOADS_DIR / output_filename

        print(
            f"Creating zip for '{style_name_zip}' ({min_zoom_zip}-{max_zoom_zip}): {output_path} ..."
        )
        try:
            if any(style_cache_dir_zip.iterdir()):
                zip_path_str = create_zip(style_cache_dir_zip, style_name_zip)
                final_zip_path = Path(zip_path_str)
                if output_path.exists():
                    output_path.unlink()
                final_zip_path.rename(output_path)
                print(f"  Zip file created successfully: {output_path}")
                zip_success_count += 1
            else:
                print(
                    f"  Skipping zip for '{style_name_zip}': No tiles found in cache directory."
                )
        except Exception as e:
            print(f"  Error creating zip file for '{style_name_zip}': {e}")

    print(
        f"--- Zip creation finished ({zip_success_count}/{len(download_tasks)} successful) ---"
    )
    print("\nCLI download process finished.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Map Tile Downloader - Web UI or CLI")
    parser.add_argument("--cli", action="store_true", help="Force run in CLI mode.")
    parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
        help="Bounding box for tile download (required in CLI mode).",
    )
    parser.add_argument(
        "--min-zoom",
        type=int,
        help="Default minimum zoom level if not specified per style.",
    )
    parser.add_argument(
        "--max-zoom",
        type=int,
        help="Default maximum zoom level if not specified per style.",
    )
    parser.add_argument(
        "--downloads",
        required=True,
        nargs="+",
        metavar="STYLE[:MIN-MAX]",
        help='Download task(s). Format: "StyleName" or "StyleName:MinZoom-MaxZoom".',
    )
    parser.add_argument(
        "--convert-8bit",
        action="store_true",
        help="Convert downloaded tiles to 8-bit palette PNG.",
    )

    args = parser.parse_args()

    is_cli_mode = bool(args.downloads)

    if is_cli_mode:
        run_cli_download(args)
    else:
        print("Starting Flask web server. Use --bbox and --downloads for CLI mode.")
        CACHE_DIR.mkdir(exist_ok=True)
        CONFIG_DIR.mkdir(exist_ok=True)
        DOWNLOADS_DIR.mkdir(exist_ok=True)
        socketio.run(app, debug=True, use_reloader=False)
