Feature: the window

  A fixed instrument panel that opens maximized, states what a run will do before the button is pressed, and
  never computes a figure the engine could have measured.

  Scenario: the run control needs something to run
    Given an empty queue
    When the window is drawn
    Then the run control is present and disabled
    And the sentence beside it says what to add

  Scenario: a file is measured and planned when it is added
    Given a file the engine can read
    When it is added to the queue
    Then its row carries what it measured and what it will become
    And the plan's gain and cautions are on screen before anything is pressed

  Scenario: every number the window shows is one the engine measured
    Given any figure on screen
    Then it arrived in a record the engine published
    And the interface performs no arithmetic on levels, durations or frame counts
    Because a number derived twice is a number that can disagree with itself

  Scenario: the chain's figures are disabled where there is no chain
    Given the strategy that has no chain
    When the settings are drawn
    Then the drive and the ceiling are disabled
    And choosing the operator's chain enables them at the operator's own values

  Scenario: a target the chain cannot reach is flagged before the run
    Given the operator's chain and a target above its limiter's working level
    When the target is set
    Then the prose under the strategy says what the chain can reach
    And it is drawn as a caution rather than as a value

  Scenario: the interface refuses a choice the machine cannot serve
    Given a machine whose ffmpeg has no MP3 encoder
    When the settings are drawn
    Then the MP3 box is disabled
    And the format is dropped where the request is built
    Because disabling a control does not stop the state from holding the choice

  Scenario: nothing scrolls and nothing clips at the declared minimum
    Given a window at its declared minimum
    Then the frame does not scroll
    And no control runs past the right edge
    And the queue, the log and the report scroll inside themselves
