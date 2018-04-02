class AudioTag(object):
	rating = 0
	genre = ''

	def __init__(self, artist='', album='', title=''):
		self.album = album
		self.artist = artist
		self.title = title

	def __str__(self):
		sep = ' - '
		return self.artist + sep + self.album + sep + self.title
