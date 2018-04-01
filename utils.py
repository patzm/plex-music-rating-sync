"""
This file contains helper functions
"""

def format_plexapi_track(track):
	return ' - '.join([track.artist().title, track.album().title, track.title])
