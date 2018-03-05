from AudioTag import AudioTag


class MediaPlayer:
	name = ''
	rating_maximum = 5

	@staticmethod
	def get_5star_rating(rating):
		return rating * 5

	def get_normed_rating(self, rating):
		return rating / self.rating_maximum

	def read_tracks(self):
		raise NotImplementedError()


class MediaMonkey(MediaPlayer):
	def __init__(self):
		self.name = 'MediaMonkey'
		self.rating_maximum = 100

	def read_tracks(self):
		print('Reading tracks from the {} player'.format(self.name))

		import win32com.client

		sdb = None
		try:
			sdb = win32com.client.Dispatch("SongsDB.SDBApplication")
			sdb.ShutdownAfterDisconnect = False
		except Exception:
			print('No scripting interface to MediaMonkey can be found. Exiting...')
			exit(1)

		it = sdb.Database.QuerySongs("Rating > 0")
		tags = []
		counter = 0
		while not it.EOF:
			tag = AudioTag(it.Item.Artist.Name, it.Item.Album.Name, it.Item.Title)
			tag.rating = self.get_normed_rating(it.Item.Rating)
			tags.append(tag)

			counter += 1
			it.Next()

		print('Read {} tracks with a rating > 0'.format(counter + 1))
		return tags


class PlexPlayer(MediaPlayer):
	def __init__(self):
		self.name = 'PlexPlayer'
		self.rating_maximum = 10

	def read_tracks(self):
		raise NotImplementedError()
