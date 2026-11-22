run "the_example_is_valid" {
  command = plan
  variables {
    dir = "../../settings/example"
  }
  assert {
    condition     = length(output.errors) == 0
    error_message = join("; ", output.errors)
  }
  assert {
    condition     = output.deployments["support/chat/global"].order == 2 && output.fallbacks["orders-fast"] == "orders-backup"
    error_message = "the failover order or the fallback is wrong"
  }
}

run "broken_settings_stop_the_plan" {
  command = plan
  variables {
    dir = "./testdata/broken"
  }
  expect_failures = [output.teams, output.deployments]
}
