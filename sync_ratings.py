#!/usr/bin/env python3

import abc
import argparse
from enum import Enum, auto
from fuzzywuzzy import fuzz
import locale
import logging
import numpy as np
from plexapi.audio import Track

from AudioTag import AudioTag
from MediaPlayer import MediaPlayer, MediaMonkey, PlexPlayer
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


class PlexSync:
	log_levels = {
		'CRITICAL': logging.CRITICAL,
		'ERROR': logging.ERROR,
		'WARNING': logging.WARNING,
		'INFO': logging.INFO,
		'DEBUG': logging.DEBUG
	}

	def __init__(self, options):
		self.logger = logging.getLogger('PlexSync')
		self.options = options
		self.setup_logging()
		self.local_player = self.get_player()
		self.remote_player = PlexPlayer()
		self.local_player.dry_run = self.remote_player.dry_run = self.options.dry

	def get_player(self):
		_player = self.options.player.lower()
		supported_players = {MediaMonkey.name()}
		if _player == MediaMonkey.name().lower():
			return MediaMonkey()
		else:
			self.logger.error('Valid players: {}'.format(', '.join(supported_players)))
			self.logger.error('Unsupported player selected: {}'.format(self.options.player))
			exit(1)

	def setup_logging(self):
		ch = logging.StreamHandler()
		formatter = logging.Formatter(
			fmt="[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s",
			datefmt="%H:%M:%S"
		)
		ch.setFormatter(formatter)

		level = -1
		if isinstance(self.options.log, str):
			try:
				level = self.log_levels[self.options.log.upper()]
			except KeyError:
				pass
		elif isinstance(self.options.log, int):
			if 0 <= self.options.log <= 50:
				level = self.options.log

		if level < 0:
			print('Valid logging levels specified by either key or value:{}'.format('\n\t'.join(
				'{}: {}'.format(key, value) for key, value in self.log_levels.items()))
			)
			raise RuntimeError('Invalid logging level selected: {}'.format(level))
		else:
			ch.setLevel(level)
			self.logger.setLevel(level)
			self.logger.addHandler(ch)

	def sync(self):
		self.local_player.connect()
		self.remote_player.connect(self.options.server, self.options.username, password=self.options.passwd)
		self.sync_tracks()
		self.sync_playlists()

	def sync_tracks(self):
		tracks = self.local_player.search_tracks(query='Rating > 0')
		sync_pairs = [TrackPair(self.local_player, self.remote_player, track) for track in tracks]

		self.logger.info('Matching local tracks with remote player')
		matched = 0
		for pair in sync_pairs:
			if pair.match(): matched += 1
		self.logger.info('Matched {}/{} tracks'.format(matched, len(sync_pairs)))

		if self.options.dry: self.logger.info('Running a DRY RUN. No changes will be propagated!')
		pairs_need_update = [pair for pair in sync_pairs if pair.sync_state is SyncState.NEEDS_UPDATE]
		self.logger.info('Synchronizing {} matching tracks without conflicts'.format(len(pairs_need_update)))
		for pair in pairs_need_update:
			pair.sync()

		pairs_conflicting = [pair for pair in sync_pairs if pair.sync_state is SyncState.CONFLICTING]
		self.logger.info('{} pairs have conflicting ratings'.format(len(pairs_conflicting)))
		for pair in pairs_conflicting:
			pair.resolve_conflict()

	def sync_playlists(self):
		pass


def parse_args():
	parser = argparse.ArgumentParser(description='Synchronizes ID3 music ratings with a Plex media-server')
	parser.add_argument('--dry', action='store_true', help='Does not apply any changes')
	parser.add_argument('--log', default='warning', help='Sets the logging level')
	parser.add_argument('--passwd', type=str, help='The password for the plex user. NOT RECOMMENDED TO USE!')
	parser.add_argument('--player', type=str, required=True, help='Media player to synchronize with Plex')
	parser.add_argument('--server', type=str, required=True, help='The name of the plex media server')
	parser.add_argument('--username', type=str, required=True, help='The plex username')
	return parser.parse_args()


if __name__ == "__main__":
	locale.setlocale(locale.LC_ALL, '')
	args = parse_args()
	sync_agent = PlexSync(args)
	sync_agent.sync()
