run "the_example_is_valid" {
  command = plan
  variables {
    dir = "../../settings/example"
  }
  expect_failures = [check.key_maker] # a schema 1 file keeps the 1.0.0 key way, and is warned
  assert {
    condition     = length(output.errors) == 0
    error_message = join("; ", output.errors)
  }
  assert {
    condition     = output.teams["orders"].cloud == "gcp" && output.schema == 1 && output.key_maker == "terraform"
    error_message = "a 1.0.0 file must read as Google Cloud, schema 1"
  }
  assert {
    condition     = output.deployments["support/chat/global"].order == 2 && output.fallbacks["orders-fast"] == "orders-backup"
    error_message = "the failover order or the fallback is wrong"
  }
}

run "people_get_a_handle_and_a_share" {
  command = plan
  variables {
    dir = "./testdata/people"
  }
  expect_failures = [check.key_maker]
  assert {
    condition     = output.teams["eng"].members[0].handle == "ann-lee" && output.teams["eng"].members[1].daily_usd == null && length(output.errors) == 0 && output.team_safety["eng"] == "strict" && output.safety["strict"].injection == "LOW_AND_ABOVE" && output.capture["eng"].days == 30 && output.agents_on && output.teams["eng"].agents[0].stopped == false
    error_message = jsonencode(output.teams["eng"].members)
  }
}

run "a_tool_name_cannot_take_another_models_name_or_go_unmapped" {
  command = plan
  variables {
    dir = "./testdata/sneaky"
  }
  expect_failures = [output.teams, output.deployments, check.key_maker]
}

run "what_this_version_cannot_serve_is_refused" {
  command = plan
  variables {
    dir = "./testdata/later"
  }
  expect_failures = [output.teams, output.deployments]
}

run "broken_settings_stop_the_plan" {
  command = plan
  variables {
    dir = "./testdata/broken"
  }
  expect_failures = [output.teams, output.deployments, check.key_maker]
}
