class ShowHelp:
    ConflictResolution = """
=== Help: Conflict Resolution Strategy ===

Audio files can contain rating data from multiple sources — for example, MediaMonkey and Windows Media Player may each store their own rating using different tags.

When this script detects more than one rating for the same track, this strategy determines how to pick one:

  - HIGHEST: Keep the highest rating.
    Example: Two media players stored ratings of 2.0 and 3.5 → Result is 3.5

  - LOWEST: Keep the lowest rating.
    Example: Ratings were 1.0 and 4.5 → Result is 1.0

  - AVERAGE: Use the average of all ratings.
    Example: Ratings were 2.0 and 4.0 → Result is 3.0

  - PRIORITIZED_ORDER: Use the tag from your specified priority list.
    Example: You specify MediaMonkey > Windows → MediaMonkey's rating is used.

  - CHOICE: You will be prompted to choose manually for each conflict.

Use 'Show conflicts' to inspect which ratings were found for a given track.
"""

    TagPriority = """
=== Help: Tag Priority Order ===

When multiple media players have written ratings to the same file, this list determines which one takes priority.

You'll see a list of known player tags found in your files.
Enter the numbers in order of preference, separated by commas.

Example:
  If you enter: 2,1
  It means: try player #2's rating first; if it's not available, use #1.

This is only used if you selected the 'PRIORITIZED_ORDER' strategy above.
"""

    WriteStrategy = """
=== Help: Write Strategy ===

Tags are metadata entries inside your audio files — they store values like artist, album, and rating.
Different media players store ratings using different tags. For example:

  - MediaMonkey → POPM:no@email
  - Windows Media Player → POPM:Windows Media Player 9 Series

This setting controls **which** tags get updated with the resolved rating:

  - WRITE_ALL: Write to every known tag.
    WARNING: This can overwrite ratings from other users or players.
    Example: You overwrite both MediaMonkey and Windows ratings with your new value.

  - WRITE_EXISTING: Only update tags that are already present.
    WARNING: Still overwrites values in those tags.
    Example: If a MediaMonkey tag exists, its value will be replaced.

  - WRITE_DEFAULT: Only write to your chosen preferred player tag.
    Example: You choose MediaMonkey → only the POPM:no@email tag is updated.

  - OVERWRITE_DEFAULT: First remove all rating tags, then write only to the preferred tag.
    WARNING: Destroys all other rating data.

Use 'WRITE_DEFAULT' if you are unsure — it's the safest choice.
"""

    PreferredPlayerTag = """
=== Help: Preferred Player Tag ===

Each media player stores its rating in a specific tag.
When using a strategy like WRITE_DEFAULT or OVERWRITE_DEFAULT, the system needs to know which one to use.

Choose the tag that matches the player you use most:

  - MediaMonkey → POPM:no@email
  - Windows Media Player → POPM:Windows Media Player 9 Series
  - MusicBee → POPM:MusicBee
  - Winamp → POPM:rating@winamp.com
  - Generic/Text-based → TXXX:RATING

Example:
  If you primarily use MediaMonkey, choose the corresponding tag.
  This ensures your ratings are written in a format that MediaMonkey can read.
"""

    SyncOptions = """
=== Help: Sync Menu ===

This menu lets you manage how ratings are synchronized between your source and destination libraries.
All actions apply to discovered tracks — those that were matched between both libraries.

Menu Options:

  • Sync now:
      Apply rating updates based on your current filter settings.
      This typically includes unrated destination tracks and/or tracks with rating conflicts.

  • Change sync filter:
      Adjust what kinds of track pairs are eligible for syncing — such as whether to include unrated tracks,
      set a minimum match quality, or reverse the sync direction.

  • Manually resolve rating conflicts:
      Step through each discovered conflict and choose how to resolve it (use source, use destination, or set a custom rating).

  • View track pair details:
      Browse discovered tracks grouped by match quality or update status. Useful for inspection before syncing.

  • Cancel and exit:
      Leave this menu without applying any changes.
"""
    SyncFilter = """
=== Help: Sync Filter ===

This menu lets you control which discovered tracks are eligible for syncing.

Options:

  • Reverse sync direction:
      Change whether ratings sync from source → destination or the opposite.

  • Sync tracks with rating conflicts:
      Enable or disable syncing for discovered track pairs where both sides have different ratings.
      When enabled, these conflicts will be included in sync actions.

  • Sync tracks where the destination is unrated:
      Includes discovered tracks where the configured destination player (from your config file) has no rating.
      Note: this is always based on the configured destination — it does not change if you reverse the sync direction.

  • Select minimum match quality:
      Controls which track pairs are eligible for syncing based on their match score.

      Tracks are matched using a weighted similarity score based on:
        - Artist name
        - Album name
        - Track title
        - Track number
        - File path

      Match quality is grouped into:
        - Perfect: nearly identical across all fields
        - Good: high similarity in core fields
        - Poor: significant metadata differences, but identical track numbers or paths

      By default, tracks with Poor or better quality are eligible for syncing.
      This threshold represents the minimum confidence level that the two tracks refer to the same file — even when metadata differs.

  • Cancel and return:
      Leave this menu without changing the current sync filter.
"""
    MatchQuality = """
=== Help: Match Quality ===

Match quality determines how closely a track in the source library matches one in the destination library.
Each discovered track pair is assigned a score based on the similarity of the following fields:

  • Artist name
  • Album name
  • Track title
  • Track number
  • File path

The better the alignment, the higher the score.

Match Categories:

  • Perfect:
      All fields match exactly or near-exactly.
      → Example: "Coldplay - Parachutes - Yellow" in both libraries.

  • Good:
      Minor metadata differences, but core values align.
      → Example: "Kendrick Lamar - DAMN. - LOVE."
           vs. "Kendrick Lamar - DAMN. - LOVE. (feat. Zacari)"

      These may differ in punctuation, casing, or “featuring” credits,
      but still represent a strong match based on artist, album, and file path.

  • Poor:
      Major differences in metadata, but identical track numbers or file paths (usually file name and folder structure match).
      This is still considered safe for syncing because the path provides strong identity.

      → Example:
         Source: "<blank> - <black> - radio_head_01_no_surprises (track: 1)"
         Destination: "Radiohead - OK Computer - No Surprises (track: 1)"
         Weak title match, but identical track number and strong file path match.
         File path: `/Music/Radiohead/OK Computer/No Surprises.mp3` in both

      Even though artist, title, and album are mismatched, the identical path implies they are the same file.

  • No Match:
      Track did not meet the minimum similarity threshold. These are not considered discovered and will not sync.

Threshold Behavior:

By default, any match rated Poor or better is eligible for syncing. This represents the minimum level of confidence
that two files refer to the same track — even if metadata differs significantly.
"""
    ManualConflictResolution = """
=== Help: Manual Conflict Resolution ===

You're resolving rating conflicts between matched tracks in your source and destination libraries.

Options:

  • Choose the source or destination rating:
      Select which rating should be applied to the track. The chosen rating will overwrite the other.

  • Enter a new rating:
      Input a custom rating manually. The new rating will be applied to both the source and destination tracks.

  • Skip this track:
      Leave this conflict unresolved for now. No rating changes will be made.

  • Cancel:
      Exit the manual resolution process. Previously resolved tracks (if any) will be kept.
"""
