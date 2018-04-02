import abc
from enum import Enum, auto
from fuzzywuzzy import fuzz
import logging
import numpy as np
from plexapi.audio import Track

from sync_items import AudioTag
from utils import *


class SyncState(Enum):
	UNKNOWN = auto()
	UP_TO_DATE = auto()
	NEEDS_UPDATE = auto()
	CONFLICTING = auto()
	ERROR = auto()


class SyncPair(abc.ABC):
	local = None
	remote = None
	candidates = []
	sync_state = SyncState.UNKNOWN

	def __init__(self, local_player, remote_player):
		self.local_player = local_player
		self.remote_player = remote_player

	@abc.abstractmethod
	def match(self):
		"""Tries to find a match on the remote player that matches the local replica as good as possible"""

	@abc.abstractmethod
	def resolve_conflict(self):
		"""Tries to resolve a conflict as good as possible and optionally prompts the user to resolve it manually"""

	@abc.abstractmethod
	def similarity(self, candidate):
		"""Determines the similarity of the local replica with the candidate replica"""

	@abc.abstractmethod
	def sync(self):
		"""Synchronizes the local and remote replicas"""


class TrackPair(SyncPair):
	rating_local = 0.0
	rating_remote = 0.0

	def __init__(self, local_player, remote_player, local_track):
		"""

		:type local_player: MediaPlayer
		:type remote_player: PlexPlayer
		:type local_track: AudioTag
		"""
		super(TrackPair, self).__init__(local_player, remote_player)
		self.logger = logging.getLogger('PlexSync.TrackPair')
		self.local = local_track

	def albums_similarity(self, remote=None):
		"""
		Determines how similar two album names are. It takes into account different conventions for empty album names.

		:type remote: str
			 optional album title to compare the album name of the local track with
		:returns a similarity rating [0, 100]
		:rtype int
		"""
		if remote is None:
			remote = self.remote
		if self.both_albums_empty(remote=remote):
			return 100
		else:
			return fuzz.ratio(self.local.album, remote.album().title)

	def both_albums_empty(self, remote=None):
		if remote is None: remote = self.remote
		return self.local_player.album_empty(self.local.album) and self.remote_player.album_empty(remote.album().title)

	def match(self):
		if self.local is None: raise RuntimeError('Local track not set')
		self.candidates = self.remote_player.search_tracks(title=self.local.title)
		if len(self.candidates) == 0:
			self.sync_state = SyncState.ERROR
			self.logger.warning('No match found for {}'.format(self.local))
			return 0
		scores = np.array([self.similarity(candidate) for candidate in self.candidates])
		ranks = scores.argsort()
		score = scores[ranks[-1]]
		self.remote = self.candidates[ranks[-1]]
		self.logger.debug('Found match with score {} for {}: {}'.format(
			score, self.local, format_plexapi_track(self.remote)
		))

		self.rating_local = self.local.rating
		self.rating_remote = self.remote_player.get_normed_rating(float(self.remote._data.attrib['userRating'])) \
			if 'userRating' in self.remote._data.attrib \
			else 0

		if self.rating_local == self.rating_remote:
			self.sync_state = SyncState.UP_TO_DATE
		elif self.rating_local == 0.0 or self.rating_remote == 0.0:
			self.sync_state = SyncState.NEEDS_UPDATE
		elif self.rating_local != self.rating_remote:
			self.sync_state = SyncState.CONFLICTING

		return score

	def resolve_conflict(self):
		return NotImplemented

	def similarity(self, candidate):
		"""
		Determines the matching similarity of @candidate with the local query track
		:type candidate: Track
		:returns a similarity rating [0.0, 100.0]
		:rtype float
		"""
		scores = np.array([fuzz.ratio(self.local.title, candidate.title),
		                   fuzz.ratio(self.local.artist, candidate.artist().title),
		                   self.albums_similarity(remote=candidate)])
		return np.average(scores)

	def sync(self):
		if self.rating_local == 0.0:
			# Propagate the rating of the remote track to the local track
			self.local_player.update_rating(self.local, self.rating_remote)
		elif self.rating_remote == 0.0:
			# Propagate the rating of the local track to the remote track
			self.remote_player.update_rating(self.remote, self.rating_local)


class PlaylistPair(SyncPair):
	def __init__(self, local_player, remote_player):
		super(PlaylistPair, self).__init__(local_player, remote_player)
		self.logger = logging.getLogger('PlexSync.TrackPair')

	def match(self):
		raise NotImplementedError

	def resolve_conflict(self):
		raise NotImplementedError

	def similarity(self, candidate):
		raise NotImplementedError

	def sync(self):
		raise NotImplementedError
