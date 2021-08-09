from typing import List


class AudioTag(object):

	def __init__(self, artist='', album='', title=''):
		self.album = album
		self.artist = artist
		self.title = title
		self.rating = 0
		self.genre = ''

	def __str__(self):
		return ' - '.join([self.artist, self.album, self.title])


class Playlist(object):

	def __init__(self, name, parent_name=''):
		"""
		Initializes the playlist with a name
		:type name: str
		:type parent_name: str
		"""
		if parent_name != '':
			parent_name += '.'
		self.name = parent_name + name
		self.tracks: List[AudioTag] = []
		self.is_auto_playlist = False

	@property
	def num_tracks(self):
		return len(self.tracks)

	def __str__(self):
		return '{}: {} tracks'.format(self.name, self.num_tracks)
