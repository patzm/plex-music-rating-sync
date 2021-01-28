import abc
from enum import Enum, auto
from fuzzywuzzy import fuzz
import logging
import numpy as np
from plexapi.audio import Track

import MediaPlayer
from sync_items import AudioTag, Playlist
from utils import *


class SyncState(Enum):
	UNKNOWN = auto()
	UP_TO_DATE = auto()
	NEEDS_UPDATE = auto()
	CONFLICTING = auto()
	ERROR = auto()


class SyncPair(abc.ABC):
	source = None
	destination = None
	sync_state = SyncState.UNKNOWN

	def __init__(self, source_player, destination_player):
		#"""
		#TODO: this is no longer true - not sure if it matters
		#:type local_player: MediaPlayer.MediaPlayer
		#:type remote_player: MediaPlayer.PlexPlayer
		#"""
		self.source_player = source_player
		self.destination_player = destination_player

	@abc.abstractmethod
	def match(self):
		"""Tries to find a match on the destination player that matches the source replica as good as possible"""

	@abc.abstractmethod
	def resolve_conflict(self):
		"""Tries to resolve a conflict as good as possible and optionally prompts the user to resolve it manually"""

	@abc.abstractmethod
	def similarity(self, candidate):
		"""Determines the similarity of the source replica with the candidate replica"""

	@abc.abstractmethod
	def sync(self):
		"""
		Synchronizes the source and destination replicas
		:return flag indicating success
		:rtype: bool
		"""

class TrackPair(SyncPair):
	rating_source = 0.0
	rating_destination = 0.0

	def __init__(self, source_player, destination_player, source_track):
		#"""
		#TODO: this is no longer true - not sure if it matters
		#:type local_player: MediaPlayer.MediaPlayer
		#:type remote_player: MediaPlayer.PlexPlayer
		#"""
		super(TrackPair, self).__init__(source_player, destination_player)
		self.logger = logging.getLogger('PlexSync.TrackPair')
		self.source = source_track

	def albums_similarity(self, destination=None):
		"""
		Determines how similar two album names are. It takes into account different conventions for empty album names.
		:type destination: str
			 optional album title to compare the album name of the source track with
		:returns a similarity rating [0, 100]
		:rtype: int
		"""
		if destination is None:
			destination = self.destination
		if self.both_albums_empty(destination=destination):
			return 100
		else:
			if self.destination_player.name()=="PlexPlayer":            
				return fuzz.ratio(self.source.album, destination.album().title)
			else:
				return fuzz.ratio(self.source.album, destination.album)

	def both_albums_empty(self, destination=None):
		if destination is None: destination = self.destination
		if self.destination_player.name()=="PlexPlayer":   
			return self.source_player.album_empty(self.source.album) and self.destination_player.album_empty(destination.album().title)
		else:
			return self.source_player.album_empty(self.source.album) and self.destination_player.album_empty(destination.album)

	def match(self, candidates=None, match_threshold=60):
	#TODO: This should be configurable
		if self.source is None: raise RuntimeError('Source track not set')
		if candidates is None:
			if self.destination_player.name()=="PlexPlayer":  
				candidates = self.destination_player.search_tracks(title=self.source.title)
			else:
				title=self.source.title
				candidates = self.destination_player.search_tracks(title=title)
		if len(candidates) == 0:
			self.sync_state = SyncState.ERROR
			self.logger.warning('No match found for {}'.format(self.source))
			return 0
		scores = np.array([self.similarity(candidate) for candidate in candidates])
		ranks = scores.argsort()
		score = scores[ranks[-1]]
		if score < match_threshold:
			self.sync_state = SyncState.ERROR
			self.logger.debug('Score of best candidate {} is too low: {} < {}'.format(
				format_plexapi_track(candidates[ranks[-1]]), score, match_threshold
			))
			return score

		self.destination = candidates[ranks[-1]]
		if self.destination_player.name()=="PlexPlayer": 
			self.logger.debug('Found match with score {} for {}: {}'.format(
				score, self.source, format_plexapi_track(self.destination)
			))
		else:
			#TODO: this needs generized to handle multiple local players
			self.logger.debug('Found match with score {} for {}: {}'.format(
				score, self.source, format_mediamonkey_track(self.destination)
			))

		self.rating_source = self.source.rating
		if self.destination_player.name()=="PlexPlayer": 
			self.rating_destination = self.destination_player.get_normed_rating(self.destination.userRating)
		else:
			self.rating_destination = self.destination.rating

		if self.rating_source == self.rating_destination:
			self.sync_state = SyncState.UP_TO_DATE
		elif self.rating_source == 0.0 or self.rating_destination == 0.0:
			self.sync_state = SyncState.NEEDS_UPDATE
		elif self.rating_source != self.rating_destination:
			self.sync_state = SyncState.CONFLICTING
			self.logger.warning('Found match with conflicting ratings: {} (Source: {} | Destination; {})'.format(
				self.source, self.rating_source, self.rating_destination)
			)

		return score

	def resolve_conflict(self):
		return NotImplemented

	def similarity(self, candidate):
		"""
		Determines the matching similarity of @candidate with the source query track
		:type candidate: Track
		:returns a similarity rating [0.0, 100.0]
		:rtype: float
		"""
		if self.destination_player.name()=="PlexPlayer": 
			scores = np.array([fuzz.ratio(self.source.title, candidate.title),
			                   fuzz.ratio(self.source.artist, candidate.artist().title),
			                   self.albums_similarity(destination=candidate)])
		else:
			scores = np.array([fuzz.ratio(self.source.title, candidate.title),
			                   fuzz.ratio(self.source.artist, candidate.artist),
			                   self.albums_similarity(destination=candidate)])  
		return np.average(scores)

	def sync(self):
		#change everything to source/destination
		if self.rating_destination <= 0.0:
			# Propagate the rating of the remote track to the local track
			self.destination_player.update_rating(self.destination, self.rating_source)
		else:
			return False
		return True


class PlaylistPair(SyncPair):
	#TODO: finish implementing playlist sync for MediaMonkey -> Plexfo
	remote: [Playlist]

	def __init__(self, local_player, remote_player, local_playlist):
		"""
		:type local_player: MediaPlayer.MediaPlayer
		:type remote_player: MediaPlayer.PlexPlayer
		:type local_playlist: Playlist
		"""
		super(PlaylistPair, self).__init__(local_player, remote_player)
		self.logger = logging.getLogger('PlexSync.TrackPair')
		self.local = local_playlist

	def match(self):
		"""
		If the local playlist does not exist on the remote player, create it
		:return: None
		"""
		self.remote = self.remote_player.find_playlist(title=self.local.name)

	def resolve_conflict(self):
		raise NotImplementedError

	def similarity(self, candidate):
		raise NotImplementedError

	def sync(self):
		"""
		This sync routine is non-destructive and one-way. It will propagate local additions to the remote. Replicas
		existing only on the remote will not be removed or propagated to the local replica.
		:return: flag indicating success
		:rtype: bool
		"""
		self.logger.info('Synchronizing playlist {}'.format(self.local.name))
		track_pairs = [TrackPair(self.local_player, self.remote_player, track) for track in self.local.tracks]
		for pair in track_pairs:
			pair.match()

		if self.remote is None: # create a new playlist with all tracks
			remote_tracks = [pair.remote for pair in track_pairs if pair.remote is not None]
			self.remote = self.remote_player.create_playlist(self.local.name, remote_tracks)
		else: # playlist already exists, check which items need to be updated
			remote_tracks = self.remote.items()
			for pair in track_pairs:
				if pair.remote not in remote_tracks:
					self.remote_player.update_playlist(self.remote, pair.remote, True)

		return True

