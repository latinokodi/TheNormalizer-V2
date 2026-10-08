Feature: the sound, the strategies and the files written beside it

  Three ways to reach the target, two figures the operator owns, and the extra files a run can write. The
  strategies are not interchangeable and the difference is a measurement rather than a preference: a limiter
  is a ceiling, so a strategy containing one cannot deliver an arbitrary target.

  Scenario: the strategy that reaches every target is the default
    Given a request that names no strategy
    When a plan is built
    Then the strategy is one gain and nothing else
    Because a limiter clamps everything driven into it to about its own ceiling less three decibels

  Scenario: the operator's chain is reproduced rather than improved on
    Given a request whose strategy is the operator's chain
    When the filtergraph is built
    Then it carries the compressor's threshold, its ratio, its attack and its release
    And it carries the limiter's release and its ceiling
    And the limiter's automatic level is off
    Because that default adds about three decibels on top of the ceiling, by an amount that depends on the material

  Scenario: the two figures reach ffmpeg in the units ffmpeg wants
    Given a drive in decibels and a ceiling in dBFS
    When the chain is built
    Then the ceiling is converted from dBFS to the linear amplitude the option wants
    And the drive is a fader in front of the chain rather than the compressor's own option
    Because that option is a multiplier whose range starts at one, so it cannot attenuate

  Scenario: a target the chain cannot reach is named before the run
    Given a target above the chain's limiter working level
    When a plan is built
    Then the plan carries a caution naming the level the chain can reach
    And it says which of the two strategies can reach the target instead

  Scenario: the sound keeps the source's rate and channel count
    Given any source
    When a run produces the sound
    Then nothing resamples it and nothing remixes it
    And the finished file is checked against the source's own rate and channel count

  Scenario: the sound files are the sound the master plays
    Given a request that asks for a WAV, an MP3, or both
    When the run is planned
    Then each export reads the finished master or the export before it
    And no export reads one of the sources
    And no export applies a filter of its own
    And the MP3 is refused unless the WAV is asked for with it
    Because it is encoded from that WAV

  Scenario: an occupied sound-file name is skipped rather than refused
    Given a destination whose sound files are already there
    When the run is planned
    Then the exports are skipped together
    And the plan says which file is in the way
    And the master still goes out

  Scenario: the run reports the files it wrote
    Given a run whose exports were skipped
    When the outcome is reported
    Then the files it wrote are empty of those exports
    Because a file at that path may be from an earlier run

  Scenario: a name that is taken steps aside
    Given a source whose normalized name is already occupied
    When the destination is settled
    Then the name gains a number and is used
    And the source itself is never the destination
