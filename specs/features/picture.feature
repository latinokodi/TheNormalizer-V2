Feature: the picture is copied and proved to be

  A stream copy reproduces a source's compressed packets exactly, so the decoded frames in the output must
  be identical to the frames in the file they came from. That is a thing a copy guarantees and a re-encode
  cannot fake — which is what makes "the picture was copied" a claim with a check behind it rather than a
  setting that was written into a command.

  Scenario: every command that writes the picture copies it
    Given a plan for a file with a picture
    When the commands it will run are built
    Then every command that maps the source's video carries a copy
    And exactly one command maps it

  Scenario: the picture is fetched from the source and the sound from the fader
    Given a plan whose sound is produced by a separate pass
    When the command that puts the two together is built
    Then its first input is the source and its second is the finished sound
    And the video is mapped from the first and the audio from the second

  Scenario: copied frames are compared against the file they came from
    Given a finished file whose picture was copied
    When the result is verified
    Then frames are sampled from the output and from the source
    And each sampled pair is compared by checksum
    And the number that matched is reported

  Scenario: frames are matched by presentation time and not by position
    Given two files that share a picture but not a keyframe layout
    When both are decoded from the same seek point
    Then the two decodes begin at different frames
    And the comparison matches them by presentation time
    Because a frame's presentation time is what it is, and its position in a seeked decode is an accident

  Scenario: a file with no picture has no picture to compare
    Given a sound-only source
    When the result is verified
    Then the picture check is reported as not checked
    And it is not reported as a pass
    Because a check that could not run is not the same as one that held

  Scenario: the sound is checked to decode
    Given a finished file
    When the result is verified
    Then its sound is decoded in full
    And a sound that will not decode is a failure
    Because a container can accept a codec it has no framing for, and the file then probes correctly and plays as nothing

  Scenario: the container is checked against the muxer that was asked for
    Given a finished file
    When the result is verified
    Then the file's own format name is compared against the muxer it was written by
    And a mismatch is a failure
