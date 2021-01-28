import abc
import logging
import getpass
import pandas as pd
import plexapi.playlist
import plexapi.audio
from plexapi.exceptions import BadRequest, NotFound
from plexapi.myplex import MyPlexAccount
import time
from typing import List, Optional

from sync_items import AudioTag, Playlist
from utils import *

class MediaPlayer(abc.ABC):
	album_empty_alias = ''
	dry_run = False
	reverse = False
	full = True
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

	def connect(self, *args, **kwargs):
		return NotImplemented

	@abc.abstractmethod
	def create_playlist(self, title: str, tracks: List[object]):
		"""
		Creates a playlist unless in dry run
		"""

	@staticmethod
	def get_5star_rating(rating):
		return rating * 5

	def get_native_rating(self, normed_rating):
		return normed_rating * self.rating_maximum

	def get_normed_rating(self, rating):
		if rating <0: rating = 0
		return rating / self.rating_maximum

	@abc.abstractmethod
	def read_playlists(self):
		"""

		:return: a list of all playlists that exist, including automatically generated playlists
		:rtype: list<Playlist>
		"""

	@abc.abstractmethod
	def find_playlist(self, **nargs):
		"""

		:param nargs:
		:return: a list of playlists matching the search parameters
		:rtype: list<Playlist>
		"""

	@abc.abstractmethod
	def search_tracks(self, **nargs):
		"""Returns all tracks matching a particular query"""

	@abc.abstractmethod
	def updated_tracks(self, **nargs):
		"""Returns all tracks that have been updated since last execution"""

	@abc.abstractmethod
	def update_playlist(self, playlist, track, present):
		"""Updates the playlist, unless in dry run
		:param playlist:
			The playlist native to this player that shall be updated
		:param track:
			The track to update
		:type present: bool
		"""

	@abc.abstractmethod
	def update_rating(self, track, rating):
		"""Updates the rating of the track, unless in dry run"""

	def __hash__(self):
		return hash(self.name().lower())

	def __eq__(self, other):
		if not isinstance(other, type(self)): return NotImplemented
		return other.name().lower() == self.name().lower()

class MediaMonkey(MediaPlayer):
#TODO logging needs to be updated to reflect whether MediaMonkey is source or destination
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

	def create_playlist(self, title, tracks):
		raise NotImplementedError

	def find_playlist(self, **nargs):
		raise NotImplementedError

	def updated_tracks(self, new_tracks):
		rated_tracks = []
		tags=[]
		while not new_tracks.EOF:
			rated_tracks.append(self.read_track_metadata(new_tracks.Item,textonly=True))
			new_tracks.Next()
		df_rated = pd.DataFrame(data=rated_tracks,columns=["Artist","Album","Title","Rating","ID"])

		try:
			df_old_rated=pd.read_pickle("./.MediaMonkey_rated.pickle")
		except:
			df_old_rated=pd.DataFrame()
			self.logger.debug('Previously rated tracks not found.  Syncing all tracks.')
		df_updated=df_rated[~df_rated.isin(df_old_rated.to_dict('list')).all(1)]
		for index,row in df_updated.iterrows():
			tag = AudioTag(row['Artist'], row['Album'],row['Title'])
			tag.rating = row['Rating']
			tag.ID = row['ID']
			tags.append(tag)
		if not self.dry_run: 
			df_rated.to_pickle("./.MediaMonkey_rated.pickle")
		return tags        
        
	def read_child_playlists(self, parent_playlist):
		"""
		:rtype: list<Playlist>
		"""
		playlists = []
		for i in range(len(parent_playlist.ChildPlaylists)):
			_playlist = parent_playlist.ChildPlaylists[i]
			playlist = Playlist(_playlist.Title, parent_name=parent_playlist.Title)
			playlists.append(playlist)
			playlist.is_auto_playlist = _playlist.isAutoplaylist
			if playlist.is_auto_playlist:
				self.logger.debug('Skipping to read tracks for auto playlist {}'.format(playlist.name))
				continue

			for j in range(_playlist.Tracks.Count):
				playlist.tracks.append(self.read_track_metadata(_playlist.Tracks[j]))

			if len(_playlist.ChildPlaylists):
				playlists.extend(self.read_child_playlists(_playlist))

		return playlists

	def read_playlists(self):
		self.logger.info('Reading playlists from the {} player'.format(self.name()))
		root_playlist = self.sdb.PlaylistByTitle('')
		playlists = self.read_child_playlists(root_playlist)
		self.logger.info('Found {} playlists'.format(len(playlists)))
		return playlists

	def read_track_metadata(self, track, textonly=False):
		if textonly:
			tag=[track.Artist.Name, track.Album.Name, track.Title,self.get_normed_rating(track.Rating),track.ID]
		else:
			tag = AudioTag(track.Artist.Name, track.Album.Name, track.Title)
			tag.rating = self.get_normed_rating(track.Rating)
			tag.ID = track.ID
		return tag

	def search_tracks(self, **kwargs):
		"""
		Searches the MediaMonkey music library for tracks matching the artist and track title
		:param kwargs:
			See below

		:keyword Arguments:
			* *query*  (``str``)   -- MediaMonkey query string
			* *rating* (``bool``)  -- Search for tracks that have a rating
			* *title*  (``str``)   -- Search by Track title

		:return: a list of matching tracks
		:rtype: list<sync_items.AudioTag>
		"""
		title = kwargs.get('title')
		rating = kwargs.get('rating')
		if not (rating):
			rating = False
		query = kwargs.get('query')
		
		if title: 
			title=title.replace('"', r'""')
			query = f'SongTitle = "{title}"'
		elif rating:
			query = 'Rating > 0'
		self.logger.debug('Executing query [{}] against {}'.format(query, self.name()))

		if not self.reverse:
			self.logger.info('Reading tracks from the {} player'.format(self.name()))
		it = self.sdb.Database.QuerySongs(query)
		if (not self.full) & rating:
			tags=self.updated_tracks(it)
			counter=len(tags)
		else:
			tags = []
			counter = 0
			while not it.EOF:
				tags.append(self.read_track_metadata(it.Item))
				counter += 1
				it.Next()

		if rating:
			self.logger.info('Read {} tracks with a rating > 0'.format(counter))
		return tags

	def update_playlist(self, playlist, track, present):
		raise NotImplementedError

	def update_rating(self, track, rating):
		self.logger.debug('Updating rating of track "{}" to {} stars'.format(
			format_mediamonkey_track(track), self.get_5star_rating(rating))
		)
		if not self.dry_run: 
			song = self.sdb.Database.QuerySongs('ID='+str(track.ID))
			song.Item.Rating = self.get_native_rating(rating)
			song.Item.UpdateDB()

class PlexPlayer(MediaPlayer):
#TODO logging needs to be updated to reflect whether Plex is source or destination
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

	def connect(self, server, username, password='',token=''):
		self.logger.info(f'Connecting to the Plex Server {server} with username {username}.')
		connection_attempts_left = self.maximum_connection_attempts
		while connection_attempts_left > 0:
			time.sleep(1)  # important. Otherwise, the above print statement can be flushed after
			if (not password) & (not token):
				password = getpass.getpass()
			try:
				if (password):
					self.account = MyPlexAccount(username=username, password=password)
				elif (token):
					self.account = MyPlexAccount(username=username, token=token)                    
				break
			except NotFound:
				print(f'Username {username}, password or token wrong for server {server}.')
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

	def read_track_metadata(self, track,textonly=False):
		if textonly:
			tag=[track.grandparentTitle, track.parentTitle, track.title,self.get_normed_rating(track.userRating)]
		else:
			tag = AudioTag(track.grandparentTitle, track.parentTitle, track.title)
			tag.rating = self.get_normed_rating(track.userRating)
		return tag

	def create_playlist(self, title, tracks: List[plexapi.audio.Track]) -> Optional[plexapi.playlist.Playlist]:
		self.logger.info('Creating playlist {} on the server'.format(title))
		if self.dry_run:
			return None
		else:
			if tracks is None or len(tracks) == 0:
				self.logger.warning('Playlist {} can not be created without supplying at least one track. Skipping.'.format(title))
				return None
			return self.plex_api_connection.createPlaylist(title=title, items=tracks)

	def read_playlists(self):
		raise NotImplemented

	def updated_tracks(self, new_tracks):
		rated_tracks = []
		tags=[]
		for track in new_tracks:
			rated_tracks.append(self.read_track_metadata(track,textonly=True))
		df_rated = pd.DataFrame(data=rated_tracks,columns=["Artist","Album","Title","Rating"])
		try:
			df_old_rated=pd.read_pickle("./.Plex_rated.pickle")
		except:
			df_old_rated=pd.DataFrame()
			self.logger.debug('Previously rated tracks not found.  Syncing all tracks.')
		df_updated=df_rated[~df_rated.isin(df_old_rated.to_dict('list')).all(1)]
		for index,row in df_updated.iterrows():
			tag = AudioTag(row['Artist'], row['Album'],row['Title'])
			tag.rating = row['Rating']
			tags.append(tag)
		if not self.dry_run: 
			df_rated.to_pickle("./.Plex_rated.pickle")
		return tags 
        
	def find_playlist(self, **kwargs) -> Optional[plexapi.playlist.Playlist]:
		"""

		:param kwargs:
			See below

		:keyword Arguments:
			* *title* (``str``) -- Playlist name

		:return: a list of matching playlists
		:rtype: list<Playlist>
		"""
		title = kwargs['title']
		try:
			return self.plex_api_connection.playlist(title)
		except NotFound:
			self.logger.debug('Playlist {} not found on the remote player'.format(title))
			return None

	def search_tracks(self, **kwargs):
		"""
		Searches the PMS music library for tracks matching the artist and track title
		:param kwargs:
			See below

		:keyword Arguments:
			* *title*  (``str``)  -- Search by Track title
			* *rating* (``bool``) -- Search for tracks that have a rating

		:return: a list of matching tracks
		:rtype: list<plexapi.audio.Track>
		"""
		title = kwargs.get('title')
		rating = kwargs.get('rating')
		if not rating:
			rating = False

		if title: 
			matches = self.music_library.searchTracks(title=title)
			n_matches = len(matches)
			self.logger.debug('Found {} match{} for query title={}'.format(n_matches, 'es' if n_matches > 1 else '', title))
		if rating:
			matches = self.music_library.searchTracks(**{'track.userRating!':'0'})
			if (not self.full) & rating:
				tags=self.updated_tracks(matches)
				counter=len(tags)
			else:
				tags = []
				counter = 0
				for x in matches:
					tags.append(self.read_track_metadata(x))
					counter += 1
			self.logger.info('Found {} tracks with a rating > 0 that need syncing'.format(counter))
			matches=tags
		return matches

	def update_playlist(self, playlist, track, present):
		"""
		:type playlist: plexapi.playlist.Playlist
		:type track: plexapi.audio.Track
		:type present: bool
		:return:
		"""
		if present:
			self.logger.debug('Adding {} to playlist {}'.format(format_plexapi_track(track), playlist.title))
			if not self.dry_run: playlist.addItems(track)
		else:
			self.logger.debug('Removing {} from playlist {}'.format(format_plexapi_track(track), playlist.title))
			if not self.dry_run: playlist.removeItem(track)

	def update_rating(self, track, rating):
		self.logger.debug('Updating rating of track "{}" to {} stars'.format(
			format_plexapi_track(track), self.get_5star_rating(rating))
		)
		if not self.dry_run: track.edit(**{'userRating.value': self.get_native_rating(rating)})
