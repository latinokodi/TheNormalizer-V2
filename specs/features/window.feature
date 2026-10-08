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

  Scenario: there are exactly two controls
    Given the window with nothing queued
    Then the level and the drive are the only settings on the panel
    Because a field that is not a decision is a question the operator has to answer or ignore

  Scenario: each control says what it does in words that need no glossary
    Given the level and the drive
    Then each carries a sentence saying what changing it will do
    And neither is labelled with a term a person has to look up

  Scenario: the level and the limiter are one number
    Given the level set to any value
    When the request is built
    Then the target and the limiter carry that one value
    Because two fields for one decision is two chances to disagree about it

  Scenario: the interface refuses a choice the machine cannot serve
    Given a machine whose ffmpeg has no MP3 encoder
    When the settings are drawn
    Then the MP3 box is disabled
    And the format is dropped where the request is built
    Because disabling a control does not stop the state from holding the choice

  Scenario: the controls are on the left and the files on the right
    Given the window is drawn
    Then the settings column begins at the left edge
    And the file column begins where the settings column ends
    And the source order is the same order, so the reading order matches the layout

  Scenario: nothing scrolls and nothing clips at the declared minimum
    Given a window at its declared minimum
    Then the frame does not scroll
    And no control runs past the right edge
    And the queue, the log and the report scroll inside themselves
