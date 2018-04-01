# plex-music-rating-sync
Plex Agents do not read music ratings when importing music files.
This makes sense from a server-side-of-view.
You don't want all users to have the same ratings.
Every user should be able to set his / her own ratings.

This project aims to provide a simple sync tool that synchronizes the track ratings and playlists with a specific PLEX user account and server.

## Current Issues
* improve matching local tracks with remote tracks.
The mixture of agents completing and changing the music metadata on the PMS causes several tracks to have slightly different metadata.
* the [PlexAPI](https://pypi.org/project/PlexAPI/) seems to be only working for the administrator of the PMS.

## Upcoming Features
* playlist sync
* bi-directional sync
* better logging
* better user-interaction with nicer dialogs
* cache synchronization conflicts to prompt the user at the end of the batch run to resolve them

## References
[PlexAPI](https://pypi.org/project/PlexAPI/) simplifies talking to a _Plex Media Server_ (PMS). 

This project uses the MediaMonkey scripting interface using Microsoft COM model.
An introduction can be found [here](http://www.mediamonkey.com/wiki/index.php/Introduction_to_scripting).
The relevant model documentation is available [here](http://www.mediamonkey.com/wiki/index.php/SDBApplication).

## Requirements
* Windows
* Python 3.5 packages:
    * [PlexAPI](https://pypi.org/project/PlexAPI/)
    * [pypiwin32](https://pypi.org/project/pypiwin32/)
    * [fuzzywuzzy](https://github.com/seatgeek/fuzzywuzzy): for fuzzy string matching
    * [python-Levenshtein](https://github.com/miohtama/python-Levenshtein) (optional): to improve performance of `fuzzywuzzy`
