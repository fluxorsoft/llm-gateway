"""Safety rules per team, inside the gateway image (LiteLLM installed):
docker run --rm -e ARMOR_TEMPLATES='{"red": "t-red"}' -v $PWD:/w --entrypoint /app/.venv/bin/python gateway:test /w/test_armor_templates.py"""
import asyncio
import unittest

from onedoorai.litellm import armor


class Probe(armor.FullArmor):
    """FullArmor without LiteLLM's set-up: only the choice of template is tested here."""

    def __init__(self):
        self._default_template = "t-default"

    async def ask(self, team):
        async def fake_request(*args, request_data=None, **kwargs):
            await asyncio.sleep(0.001)
            return self.template_id
        original = armor.ModelArmorGuardrail.make_model_armor_request
        armor.ModelArmorGuardrail.make_model_armor_request = lambda s, *a, **k: fake_request(*a, **k)
        try:
            return await self.make_model_armor_request(content="x", source="user_prompt",
                                                       request_data={"metadata": {"user_api_key_team_alias": team}})
        finally:
            armor.ModelArmorGuardrail.make_model_armor_request = original


class TemplatesTest(unittest.TestCase):
    def test_each_team_gets_its_template_and_others_the_default(self):
        p = Probe()
        self.assertEqual(asyncio.run(p.ask("red")), "t-red")
        self.assertEqual(asyncio.run(p.ask("blue")), "t-default")
        self.assertEqual(p.template_id, "t-default")  # nothing left behind

    def test_concurrent_requests_never_mix(self):
        p = Probe()

        async def many():
            teams = ["red", "blue"] * 50
            return list(zip(teams, await asyncio.gather(*(p.ask(t) for t in teams))))
        wrong = [(t, got) for t, got in asyncio.run(many()) if got != ("t-red" if t == "red" else "t-default")]
        self.assertEqual(wrong, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
