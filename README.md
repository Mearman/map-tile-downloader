# Map Tile Downloader

The Map Tile Downloader is a Flask-based web application designed to download map tiles from various sources. It allows users to select specific areas on a map, choose zoom levels, and download tiles for offline use. The application supports converting tiles to 8-bit color depth for compatibility with Meshtastic® UI Maps and provides options to view and manage cached tiles.

<img width="964" alt="image" src="https://github.com/user-attachments/assets/57b041c9-2be6-4bf4-9ed4-98df24472a47" />

## Features

- **Custom Area Downloads**: Draw polygons on the map to select specific areas for tile downloading.
- **World Basemap Downloads**: Download tiles for the entire world at zoom levels 0-7.
- **8-bit Conversion**: Option to convert downloaded tiles to 8-bit color depth for Meshtastic® UI Maps.
- **Cache Management**: View and delete cached tiles for different map styles.
- **Progress Tracking**: Real-time progress bar showing downloaded, skipped, and failed tiles.
- **Configurable Map Sources**: Easily add or modify map sources via a JSON configuration file.

## Prerequisites

- Python 3.8 or higher (For Windows, make sure that Python in installed with the ADD TO PATH option selected)
- A modern web browser (Chrome, Firefox, Edge, etc.)
- Git (for cloning the repository)

## Installation

1. Clone the Repository (or download the zip file and extract to the location of your choice):

	git clone https://github.com/mattdrum/map-tile-downloader.git
	cd map-tile-downloader

2. Install Dependencies (Optional) :
    	The application will automatically install required dependencies from requirements.txt on startup. However, you can manually install them using:

		pip install -r requirements.txt

3. Set Up Configuration (Optional, default sources are included) :

   Ensure the config/map_sources.json file is present and correctly formatted. See the Configuration section below for an example.


## Configuration
The application uses a JSON configuration file (config/map_sources.json) to define available map sources. Each entry consists of a name and a URL template for the tiles.

Example map_sources.json:

		"OpenStreetMap": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
		"OpenTopoMap": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
		"Stamen Terrain": "http://{s}.tile.stamen.com/terrain/{z}/{x}/{y}.png",
		"CartoDB Positron": "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
		"CartoDB Dark Matter": "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"


Adding a New Map Source: Simply add a new key-value pair to the JSON file with the map name and its tile URL template.


## Usage
1.	Navigate to the application directory and Run the Application:

		python src/TileDL.py

	The application will start a local server at http://localhost:5000.
	- Alternatively you may create a Batch file "StartMap.bat" to launch from windows:
 - 		@echo off
		cd /d C:\(extractlocation)\map-tile-downloader
		python scr/TileDL.py
		pause

3. 	Access the Web Interface:

	Open your web browser and navigate to http://localhost:5000.

4. 	Select Map Style:

	Choose a map style from the dropdown menu. The available options are loaded from map_sources.json.
5. 	Draw Polygons:

	Use the drawing tools to select areas on the map for which you want to download tiles.

6.	Set Zoom Levels:

	Specify the minimum and maximum zoom levels for the tiles you wish to download.

7.	Download Tiles:

	Click "Download Tiles" to start downloading tiles for the selected areas and zoom levels.
	Alternatively, click "Download World Basemap" to download tiles for the entire world at zoom levels 0-7.

8.	Monitor Progress:

	The progress bar will display the number of downloaded, skipped, and failed tiles.

9.	Manage Cache:

	Check "View cached tiles" to see outlines of cached tiles on the map.

	Use "Delete Cache" to remove cached tiles for the selected map style.


### Command-Line Interface (CLI) Usage

In addition to the web interface, the script can be run directly from the command line to download tiles, supporting multiple styles and zoom ranges simultaneously using parallel processing.

**Basic Syntax:**

```bash
python src/TileDL.py --bbox <WEST> <SOUTH> <EAST> <NORTH> --downloads <TASK_1> [<TASK_2> ...] [OPTIONS]
```

**Required Arguments:**

*   `--bbox <WEST> <SOUTH> <EAST> <NORTH>`: Specifies the geographical bounding box for the download. Coordinates are floating-point numbers (longitude, latitude).
*   `--downloads <TASK_1> [<TASK_2> ...]`: Defines one or more download tasks. Each task specifies a map style and optionally a zoom range.
    *   **Format:**
        *   `"Style Name"`: Downloads the style using the default zoom range (requires `--min-zoom` and `--max-zoom` to be set).
        *   `"Style Name:MinZoom-MaxZoom"`: Downloads the style for the specified zoom range (e.g., `"Standard OSM:10-14"`).
    *   **Note:** Style names containing spaces must be enclosed in quotes. You can list multiple tasks separated by spaces.

**Optional Arguments:**

*   `--min-zoom <ZOOM>`: Sets the default minimum zoom level if a task in `--downloads` does not specify its own range.
*   `--max-zoom <ZOOM>`: Sets the default maximum zoom level if a task in `--downloads` does not specify its own range.
*   `--convert-8bit`: If present, converts downloaded tiles to 8-bit indexed colour PNGs (useful for devices like Meshtastic). Applies to all tasks in the run.

**Behaviour:**

*   **Parallel Downloads:** All specified tile download jobs across all tasks are executed concurrently using multiple threads (currently 10 workers) for faster completion.
*   **Progress Reporting:** Overall progress percentage and an estimated time remaining (ETA), including days/hours/minutes/seconds, are displayed in the console. The ETA is calculated using a moving average of recent download times.
*   **Output:** Upon completion, a separate `.zip` file is created in the `downloads/` directory for each task specified in the `--downloads` argument. The zip files are named automatically based on the style and zoom range (e.g., `StyleName_MinZ-MaxZ.zip`).

**Examples:**

1.  **Download a single style for a specific zoom range:**
    ```bash
    python src/TileDL.py --bbox -4.9 52.6 -2.1 53.7 --downloads "CartoDB Dark Matter:10-15"
    ```

2.  **Download multiple styles, each with specific zoom ranges:**
    ```bash
    python src/TileDL.py --bbox -4.9 52.6 -2.1 53.7 --downloads "Standard OSM:10-12" "Esri World Imagery Satellite:13-16"
    ```

3.  **Download multiple styles, one with a specific range, one using default range:**
    ```bash
    python src/TileDL.py --bbox -4.9 52.6 -2.1 53.7 --min-zoom 8 --max-zoom 11 --downloads "Standard OSM:12-14" "OpenTopoMap Outdoors"
    ```
    *(This downloads "Standard OSM" for 12-14 and "OpenTopoMap Outdoors" for 8-11)*

4.  **Download with 8-bit conversion:**
    ```bash
    python src/TileDL.py --bbox -4.9 52.6 -2.1 53.7 --downloads "Standard OSM:10-13" --convert-8bit
    ```

## Contributing

We welcome contributions to improve the Map Tile Downloader! To contribute:

- Fork the Repository: Create your own fork of the project.
- Create a Feature Branch: Work on your feature or bug fix in a separate branch.
- Submit a Pull Request: Once your changes are ready, submit a pull request to the main repository.
- Coding Standards: Follow PEP 8 for Python code and ensure your code is well-documented.
- Testing: Test your changes locally before submitting a pull request.

## License

This project is licensed under the MIT License. See the  file for details.

Contact Information
For questions, suggestions, or support, please open an issue on the GitHub repository or contact k4mbd.ham@gmail.com

## Acknowledgements

- Leaflet: For the interactive map interface.
- Flask: For the web framework.
- SocketIO: For real-time communication.
- Mercantile: For tile calculations.
- Shapely: For geometric operations.
- Pillow: For image processing.

Special thanks to all contributors and the open-source community!
