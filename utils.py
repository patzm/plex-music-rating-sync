"""
This file contains helper functions
"""

def format_plexapi_track(track):
	return ' - '.join([track.artist().title, track.album().title, track.title])

def format_mediamonkey_track(track):
    #TODO: this needs generized to handle multiple local players
	return ' - '.join([track.artist, track.album, track.title])