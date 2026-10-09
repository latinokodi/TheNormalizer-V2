Feature: Suggest the settings for this file

  The window measures every file when it is added. Autodetect reads those measurements and proposes the three
  settings for *that* file, so that an operator with a quiet guest and a loud host does not have to work out
  how much evening the file needs by trial.

  The whole feature is a pure function of the measurements — no file is opened to answer it — which is why it
  can be offered instantly and why every rule below is decidable from the figures alone.

  Background:
    Given a file measured at a peak of -1.1 dBFS
    And its loudness measured at -23.4 LUFS with a loudness range of 12.8 LU

  Scenario: the file's own measurements decide the figures
    When the settings for that file are suggested
    Then the suggestion is built from what was measured rather than from a default
    And every figure in it carries the reason it was chosen

  Scenario: a file whose quiet and loud parts are far apart is evened out
    Given its quietest fifth of material is 21.8 LU below its loudest fifth
    When the settings are suggested
    Then the leveler is set to the amount by which the file exceeds a delivery spread
    And the reason names the file's own spread

  Scenario: a file that is already even is left alone
    Given its quietest fifth of material is 2.0 LU below its loudest fifth
    When the settings are suggested
    Then the leveler is switched off
    And the reason says the file is already even

  Scenario: the level is the operator's decision and not the file's
    Given the operator is delivering at -6.0 dBFS
    When the settings are suggested
    Then the target is -6.0 dBFS
    And the reason says the level is a delivery requirement rather than something read from the file

  Scenario: the make-up gain is not detected
    When the settings are suggested
    Then the make-up figure is the operator's own
    And the reason says it decides how hard the limiter is hit rather than how even the file is

  Scenario: a suggestion that cannot be made is not invented
    Given the file's loudness could not be measured
    When the settings are suggested
    Then no suggestion is offered
    And the reason is the measurement that is missing

  Scenario: a file with no sound has nothing to even out
    Given the file is digital silence
    When the settings are suggested
    Then no suggestion is offered
    And the reason says there is no sound to measure

  Scenario: asking for more evening than the tool allows is capped rather than silent
    Given its quietest fifth of material is 60.0 LU below its loudest fifth
    When the settings are suggested
    Then the leveler is set to its maximum
    And the reason says the file is wider than the tool can close

  Scenario: the same file always gets the same answer
    When the settings are suggested twice
    Then the same file gets the same answer, because a suggestion that moves on its own is one nobody can check
    And the same file gets the same answer, because a suggestion that moves on its own is one nobody can check
