class AudioTag(object):
	rating = 0
	genre = ''

	def __init__(self, artist='', album='', title=''):
		self.album = album
		self.artist = artist
		self.title = title

	def __str__(self):
		return ' - '.join([self.artist, self.album, self.title])

