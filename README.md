# plex-music-rating-sync
Plex Agents do not read music ratings when importing music files.
This makes sense from a server-side-of-view.
You don't want all users to have the same ratings.
Every use should be able to set his / her own ratings.

This project aims to provide a simple sync tool that synchronizes the track ratings and playlists with a specific PLEX user account and server.

## Current Issues
* How to change the ratings on the server (API endpoint)

## References
This project uses the MediaMonkey scripting interface using Microsoft COM model.
An introduction can be found [here](http://www.mediamonkey.com/wiki/index.php/Introduction_to_scripting).
The relevant model documentation is available [here](http://www.mediamonkey.com/wiki/index.php/SDBApplication).

## Requirements
* Windows
* Python 3.5 packages:
    * [PlexAPI](https://pypi.org/project/PlexAPI/)
    * [pypiwin32](https://pypi.org/project/pypiwin32/)
