class AudioTag(object):

	def __init__(self, artist='', album='', title=''):
		self.album = album
		self.artist = artist
		self.genre = ''
		self.rating = 0
		self.title = title

	def __str__(self):
		sep = ' - '
		return self.artist + sep + self.album + sep + self.title
