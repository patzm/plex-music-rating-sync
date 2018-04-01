import abc
import logging
import getpass
from plexapi.exceptions import BadRequest, NotFound
from plexapi.myplex import MyPlexAccount
import time

from AudioTag import AudioTag
from utils import *


class MediaPlayer(abc.ABC):
	album_empty_alias = ''
	dry_run = False
	rating_maximum = 5

	@staticmethod
	@abc.abstractmethod
	def name():
		"""
		The name of this media player
		:return: name of this media player
		:rtype: str
		"""
		return ''

	def album_empty(self, album):
		if not isinstance(album, str): return False
		return album == self.album_empty_alias

	def connect(self, *args):
		return NotImplemented

	@staticmethod
	def get_5star_rating(rating):
		return rating * 5

	def get_native_rating(self, normed_rating):
		return normed_rating * self.rating_maximum

	def get_normed_rating(self, rating):
		return rating / self.rating_maximum

	@abc.abstractmethod
	def read_playlists(self):
		"""Returns all playlists that are not automatically generated"""

	@abc.abstractmethod
	def search_tracks(self, **nargs):
		"""Returns all tracks matching a particular query"""

	@abc.abstractmethod
	def update_rating(self, track, rating):
		"""Updates the rating of the track"""

	def __hash__(self):
		return hash(self.name().lower())

	def __eq__(self, other):
		if not isinstance(other, type(self)): return NotImplemented
		return other.name().lower() == self.name().lower()


class MediaMonkey(MediaPlayer):
	rating_maximum = 100

	def __init__(self):
		super(MediaMonkey, self).__init__()
		self.logger = logging.getLogger('PlexSync.MediaMonkey')
		self.sdb = None

	@staticmethod
	def name():
		return 'MediaMonkey'

	def connect(self, *args):
		self.logger.info('Connecting to local player {}'.format(self.name()))
		import win32com.client
		try:
			self.sdb = win32com.client.Dispatch("SongsDB.SDBApplication")
			self.sdb.ShutdownAfterDisconnect = False
		except Exception:
			self.logger.error('No scripting interface to MediaMonkey can be found. Exiting...')
			exit(1)

	def read_playlists(self):
		raise NotImplementedError

	def search_tracks(self, **kwargs):
		self.logger.info('Reading tracks from the {} player'.format(self.name()))

		query = kwargs['query']
		it = self.sdb.Database.QuerySongs(query)
		tags = []
		counter = 0
		while not it.EOF:
			tag = AudioTag(it.Item.Artist.Name, it.Item.Album.Name, it.Item.Title)
			tag.rating = self.get_normed_rating(it.Item.Rating)
			tags.append(tag)

			counter += 1
			it.Next()

		self.logger.info('Read {} tracks with a rating > 0'.format(counter + 1))
		return tags

	def update_rating(self, track, rating):
		raise NotImplementedError


class PlexPlayer(MediaPlayer):
	maximum_connection_attempts = 3
	rating_maximum = 10
	album_empty_alias = '[Unknown Album]'

	def __init__(self):
		super(PlexPlayer, self).__init__()
		self.logger = logging.getLogger('PlexSync.PlexPlayer')
		self.account = None
		self.plex_api_connection = None
		self.music_library = None

	@staticmethod
	def name():
		return 'PlexPlayer'

	def connect(self, *args, password=''):
		server = args[0]
		username = args[1]
		self.logger.info('Connecting to the Plex with username "{}"'.format(server, username))
		connection_attempts_left = self.maximum_connection_attempts
		while connection_attempts_left > 0:
			time.sleep(1)  # important. Otherwise, the above print statement can be flushed after
			if not password:
				password = getpass.getpass()
			try:
				self.account = MyPlexAccount(username, password)
				break
			except NotFound:
				print('Username or password wrong'.format(server))
				password = ''
				connection_attempts_left -= 1
			except BadRequest as error:
				self.logger.warning('Failed to connect: {}'.format(error))
				connection_attempts_left -= 1
		if connection_attempts_left == 0 or self.account is None:
			self.logger.error('Exiting after {} failed attempts ...'.format(self.maximum_connection_attempts))
			exit(1)

		self.logger.info('Connecting to remote player {} on the server {}'.format(self.name(), server))
		try:
			self.plex_api_connection = self.account.resource(server).connect(timeout=5)
			self.logger.info('Successfully connected')
		except NotFound:
			# This also happens if the user is not the owner of the server
			self.logger.error('Error: Unable to connect')
			exit(1)

		self.logger.info('Looking for music libraries')
		music_libraries = {section.key: section for section in self.plex_api_connection.library.sections() if
		                   section.type == 'artist'}
		if len(music_libraries) == 0:
			self.logger.error('No music library found')
			exit(1)
		elif len(music_libraries) == 1:
			self.music_library = list(music_libraries.values())[0]
			self.logger.debug('Found 1 music library')
		else:
			print('Found multiple music libraries:')
			for key, library in music_libraries.items():
				print('\t[{}]: {}'.format(key, library.title))

			choice = input('Select the library to sync with: ')
			self.music_library = music_libraries[choice]

	def read_playlists(self):
		raise NotImplemented

	def search_tracks(self, **kwargs):
		"""
		Searches the PMS music library for tracks matching the artist and track title
		:param kwargs:
			See below

		:keyword Arguments:
			* *title* (``str``) -- Track title

		:returns a list of matching tracks
		"""
		title = kwargs['title']
		matches = self.music_library.searchTracks(title=title)
		n_matches = len(matches)
		self.logger.debug('Found {} match{} for query title={}'.format(n_matches, 'es' if n_matches > 1 else '', title))
		return matches

	def update_rating(self, track, rating):
		self.logger.debug('Updating rating of track "{}" to {} stars'.format(
			format_plexapi_track(track), self.get_5star_rating(rating))
		)
		if not self.dry_run: track.edit(**{'userRating.value': self.get_native_rating(rating)})
