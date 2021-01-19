from typing import List, Any


class AudioTag(object):
	rating = 0
	genre = ''

	def __init__(self, artist='', album='', title=''):
		self.album = album
		self.artist = artist
		self.title = title

	def __str__(self):
		return ' - '.join([self.artist, self.album, self.title])


class Playlist(object):
	tracks: List[AudioTag]
	is_auto_playlist = False
	name = ''

	def __init__(self, name, parent_name=''):
		"""
		Initializes the playlist with a name
		:type name: str
		:type parent_name: str
		"""
		if parent_name != '': parent_name += '.'
		self.name = parent_name + name
		self.tracks = []

	@property
	def num_tracks(self):
		return len(self.tracks)

	def __str__(self):
		return '{}: {} tracks'.format(self.name, self.num_tracks)

