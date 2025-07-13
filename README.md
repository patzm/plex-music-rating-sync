# plex-music-rating-sync
Plex Agents do not read music ratings from files during import, nor do they write ratings or playlists back to disk. 
This makes sense server-side — every Plex user should be able to maintain their own ratings and playlists independently.

This project began as a way to bridge that gap: a simple sync tool to push track ratings and playlists into a specific Plex user account and server, 
preserving personal curation from MediaMonkey.

Over time, it has evolved. What started as a one-way bridge has grown into a powerful any-to-any sync engine 
between Plex, MediaMonkey, and filesystem-based libraries. Ratings and playlists can now be transferred in either direction.

## Features
* synchronize: track ratings, playlists (not automatically generated)
* supported local media players: [MediaMonkey](http://www.mediamonkey.com/) and local file system
* dry run without applying any changes
* automatically or manually resolve conflicting track ratings
* logging

## FileSystem Player Support
This application now supports using a local file system directory as a media player source or destination. This allows you to:
- Sync ratings between media files on disk and Plex/MediaMonkey
- Create and manage playlists using standard M3U format
- Read/write ratings directly to media file metadata

## Requirements
* Windows
* [MediaMonkey](http://www.mediamonkey.com/) v4.x. v5.0 and above don't work, see [#23](https://github.com/patzm/plex-music-rating-sync/issues/23#issuecomment-894646791).
* _Plex Media Server_ (PMS)
* Python 3.6 or higher with packages:
    * [PlexAPI v4.2.0](https://pypi.org/project/PlexAPI/)
    * [pypiwin32](https://pypi.org/project/pypiwin32/): to use the COM interface
    * [fuzzywuzzy](https://github.com/seatgeek/fuzzywuzzy): for fuzzy string matching
    * [python-Levenshtein](https://github.com/miohtama/python-Levenshtein) (optional): to improve performance of `fuzzywuzzy`
    * [numpy](https://pypi.org/project/numpy/)
    * [ConfigArgParse](https://pypi.org/project/ConfigArgParse/)
    * [pandas](https://pandas.pydata.org/)
    * [tqdm](https://github.com/tqdm/tqdm)
    * [mutagen](https://mutagen.readthedocs.io/en/latest/)

## Installation

1. Clone this repository
   `git clone git@github.com:patzm/plex-music-rating-sync.git`
2. `cd plex-music-rating-sync`
3. Install the package and its dependencies:
   ```bash
   pip install .
   ```

Optional installations:
- For development tools (linting, type checking, testing):
  ```bash
  pip install ".[dev]"
  ```
- For improved fuzzy string matching performance:
  ```bash
  pip install ".[fuzzy]"
  ```

## Configuration
Create a `config.ini` file based on the template `config.ini.template`:
- ** Required Settings **
- `source`: Source player ("plex", "mediamonkey" or "filesystem")
- `destination`: Destination player ("plex", "mediamonkey" or "filesystem")
- `sync`: Items to sync between players, one or more of [tracks, playlists] 

### Configuration Options for PlexPlayer
- ** Required Settings **
  - `server`: The name of your Plex Media Server
  - `username`: Your Plex username  
  
- `token`: Your Plex API token (see [Finding a token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/))
- `passwd` The password for the plex user. NOT RECOMMENDED TO USE!
  
### Configuration Options for FileSystemPlayer
- ** Required Settings **
  - `path` = Path to music directory for filesystem player
  - `playlist-path` = Path to playlists directory [default: path]

## How to run
The main file is `sync_ratings.py`.
Usage description:
*Note: default values of command line arguments can be provided by editing config.ini*
```
usage: sync_ratings.py [-h] [-c] [-d] --source SOURCE --destination DESTINATION --sync [SYNC ...] [--log LOG] [--cache-mode {metadata,matches,matches-only,disabled}]
                       [--server SERVER] [--username USERNAME] [--passwd PASSWD] [--token TOKEN] [--path PATH] [--playlist-path PLAYLIST_PATH]
                       [--tag-write-strategy {write_all,write_default,overwrite_default}] [--default-tag DEFAULT_TAG]
                       [--conflict-resolution-strategy {prioritized_order,highest,lowest,average, choice}] [--tag-priority-order TAG_PRIORITY_ORDER [TAG_PRIORITY_ORDER ...]] 

Synchronizes ID3 and Vorbis music ratings between media players

required arguments:
  --source SOURCE       Source player (plex, [mediamonkey] or filesystem)
  --destination DEST    Destination player ([plex], mediamonkey or filesystem)

required Plex arguments:
  --server SERVER       The name of the plex media server
  --username USERNAME   The plex username

required File System arguments:
  --path PATH           Path to music directory for filesystem player

optional arguments:
  -h, --help            show this help message and exit
  -c                    Clear existing cache files before starting
  -d, --dry             Does not apply any changes
  --sync [SYNC ...]     Selects which items to sync: one or more of [tracks, playlists]
  --log LOG             Sets the logging level (critical, error, [warning], info, debug, trace)
  --passwd PASSWD       The password for the plex user. NOT RECOMMENDED TO USE!
  --token TOKEN         Plex API token. See Plex documentation for details
  --playlist-path PLAYLIST_PATH   Path to playlists directory for filesystem player
  --cache-mode {metadata,matches,matches-only,disabled}
                      - metadata: In-memory metadata caching only
                      - matches: Both metadata and persistent match caching
                      - matches-only: Persistent match caching only
                      - disabled: No caching
  --tag-write-strategy  Strategy for writing rating tags to files.
                        Options: write_all, write_default, overwrite_default.
  --default-tag        Canonical tag to use for writing ratings.
                        Options: Player values such as MEDIAMONKEY, WINDOWSMEDIAPLAYER, MUSICBEE, WINAMP, TEXT.
  --conflict-resolution-strategy
                        Strategy for resolving conflicting rating values.
                        Options: prioritized_order, highest, lowest, average, choice.
  --tag-priority-order  Ordered list of tag identifiers for resolving conflicts.
                        Options: MEDIAMONKEY, WINDOWSMEDIAPLAYER, MUSICBEE, WINAMP, TEXT.
```

Start the synchronization:
`./sync_ratings.py --server <server_name> --username <my@email.com|user_name>`
Using the `--dry` flag in combination with `--log DEBUG` is recommended to see what changes will be made.


## Cache Modes
The sync tool supports different caching strategies to optimize performance:

| Mode | Caches Metadata | Caches Matches | Persists Between Runs |
|------|----------------|----------------|---------------------|
| metadata | ✅ Yes | ❌ No | ❌ No |
| matches | ✅ Yes | ✅ Yes | ✅ Yes |
| matches-only | ❌ No | ✅ Yes | ✅ Yes |
| disabled | ❌ No | ❌ No | ❌ No |

- **metadata**: Caches track metadata in memory for faster lookups during sync
- **matches**: Caches both metadata and track matches, saves matches between runs
- **matches-only**: Only caches track matches persistently, no metadata caching
- **disabled**: No caching, all operations performed directly

Cache files are stored in the application directory and can be cleared using `--clear-cache`.

###  Advanced Tag Configuration (Optional)

**Note**
For most users, you don't need to configure anything about tag formats or rating strategies manually. The tool will:
- Detect which tags are used in your files
- Infer the most appropriate rating scales
- Prompt you for resolution strategy only if conflicting ratings are found
- Optionally write to `config.ini` after the first run, if necessary.

- **`tag_write_strategy`**: *Determines how rating tags are written to files.*
  - *`write_all`*: Write to all known and configured tags.
  - *`write_default`*: Only write to the `default_tag`; do not remove other tags.
  - *`overwrite_default`*: Write only to the `default_tag` and delete all other rating tags.

- **`default_tag`**: *The canonical tag to use for writing when applicable.*  
  - *Options*: Player values such as `MEDIAMONKEY`, `WINDOWSMEDIAPLAYER`, `MUSICBEE`, `WINAMP`, `TEXT`.
  - *Unknown/custom tags (e.g., `POPM:foobar@example.com`) are also allowed.*

- **`conflict_resolution_strategy`**: *Determines how to resolve conflicting rating values across tags.*
  - *`prioritized_order`*: Use the tag with the highest priority in the list below.
  - *`highest`*: Use the highest numeric rating found.
  - *`lowest`*: Use the lowest numeric rating found.
  - *`average`*: Use the average of all numeric ratings.
  - *`choice`*: Prompt user to manually enter a rating.

- **`tag_priority_order`**: *Ordered list of tag identifiers used for resolving conflicts.*  
  - *Recognized values*: Player values such as `MEDIAMONKEY`, `WINDOWSMEDIAPLAYER`, `MUSICBEE`, `WINAMP`, `TEXT`.  
  - *Unknown/custom tags (e.g., `POPM:foobar@example.com`) are also allowed.*

## Current issues
* the [PlexAPI](https://pypi.org/project/PlexAPI/) seems to be only working for the administrator of the PMS.

## Potential next features
With the current state I have completed all functionality I desired to have.
Consequently I will *not* continue development unless you request it.
I welcome anyone to join the development of this little cmd-line tool.
Just open a [new issue](https://github.com/patzm/plex-music-rating-sync/issues/new), post a pull request, or ask me to give you permissions for the repository itself. 

These are a few ideas I have for features that would make sense:

* setup routine
* parallelization
* better user-interaction with nicer dialogs
* iTunes synchronization?

## References
[PlexAPI](https://pypi.org/project/PlexAPI/) simplifies talking to a _PMS_. 

This project uses the MediaMonkey scripting interface using Microsoft COM model.
An introduction can be found [here](http://www.mediamonkey.com/wiki/index.php/Introduction_to_scripting).
The relevant model documentation is available [here](http://www.mediamonkey.com/wiki/index.php/SDBApplication).
