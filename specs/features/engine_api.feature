Feature: the engine's API

  A loopback HTTP and event-stream surface, one batch at a time, with everything a run says pushed as it
  says it. The window is the only client and it is on the same machine, so there is no authentication and
  nothing is reachable from another host.

  Scenario: the index and the route table agree
    Given the running engine
    When its index is read
    Then the routes it lists are the routes it serves

  Scenario: health names this product
    Given three applications in this family answer a health route
    When this engine is asked
    Then the answer names this product and this version
    And it reports whether the sound can be encoded at all
    And it reports whether the MP3 export can be written
    So a warm port cannot be mistaken for a slow start

  Scenario: a body that is not a request is a bad request and not a server error
    Given a body that is not an object, not JSON, or empty
    When it is sent to a route that takes one
    Then the answer is a bad request
    And it names what arrived instead
    Because a server error says the server broke, when what happened is the caller sent something that is not a request

  Scenario: a refusal names what it is missing
    Given a request with no source, or a source that is not a file
    When it is sent
    Then the answer is a bad request
    And the sentence names the field rather than the failure

  Scenario: one batch at a time
    Given a batch in flight
    When another is started
    Then the answer is a conflict whose reason is busy
    Because two encoders on one disk are not twice as fast, and the second one's progress would make the first one's bar a lie

  Scenario: a batch reports a job per file and finishes
    Given a batch of several files
    When it is started
    Then the answer carries a job identifier and a destination for each
    And the event stream reports the batch starting, each job starting and finishing, and the batch finishing

  Scenario: cancelling nothing is an answer
    Given nothing running
    When a cancel is sent
    Then the answer says nothing was cancelled
    And it is not an error

  Scenario: one output path cannot serve several files
    Given a batch of several files and a named destination
    When it is sent
    Then the answer is a bad request
    And it says that each file is written beside its own source instead

  Scenario: a subscriber that stops reading cannot stall the work
    Given a run in flight
    When a subscriber stops reading its stream
    Then every publish to it is dropped rather than waited on
    And the run continues
