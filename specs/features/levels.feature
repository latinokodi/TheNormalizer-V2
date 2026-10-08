Feature: a file is taken to a peak level the operator names

  The product's whole job, and the arithmetic it rests on. "Level" here is a **peak**: the largest absolute
  sample in the sound, in dBFS. It is not a loudness, and the features below say so where it matters.

  Scenario: a source is measured before anything is planned about it
    Given any source file
    When it is probed
    Then its container, duration, size, picture and sound are reported
    And its level is measured by decoding the whole sound
    And the plan for it is built from those measurements rather than from what the file claims

  Scenario: the level is measured rather than modelled
    Given a run whose settings name a target
    When the run produces the sound
    Then it encodes a stage pass through the master's own codec
    And it decodes that pass back and measures it
    And the master's gain is corrected by what that measurement found
    Because a codec's overshoot on decode is not a gain and cannot be modelled from the sample values

  Scenario: the delivered level is reported
    Given a finished file
    When the run is over
    Then the peak it actually has is reported
    And the signed difference from the target is reported
    And a difference inside the tolerance is a pass and one outside it is a failure

  Scenario: silence is a value rather than a very quiet signal
    Given a source whose sound is digital silence
    When a plan is built for it
    Then no gain is applied
    And the plan says why in the operator's words
    And the output is checked to be silent too

  Scenario: a target outside the range a level works in is refused
    Given a target above 0 dBFS or below −24 dBFS
    When a plan is asked for
    Then the request is refused
    And the refusal names the range it works in

  Scenario: a source with no sound cannot be normalized
    Given a file with a picture and no audio stream
    When a plan is asked for
    Then the request is refused with the reason that there is nothing to normalize
    Because this product changes the sound and copies the picture
