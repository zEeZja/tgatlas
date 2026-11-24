from datetime import datetime, timedelta, timezone
import copy
import hashlib
import unittest
from unittest.mock import patch

from src.classification_v11v2 import (
    TopicModel,
    classify,
    clean_profile,
    evaluate,
    geography,
    safe_mentions,
    select_posts,
    settings,
)


class FakeEncoder:
    """Deterministic local test vectors; not a model quality measurement."""

    settings = {"name": "fake-offline-test"}
    stats = {}

    def encode(self, texts):
        import numpy as np

        result = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            v = np.frombuffer(digest, dtype=np.uint8).astype(np.float32) + 1
            result.append(v / np.linalg.norm(v))
        return np.array(result)


def sample(texts):
    now = datetime.now(timezone.utc)
    return dict(
        username="demo",
        purpose="classification_only",
        fetched_at=now.isoformat(),
        posts=[
            dict(text=t, date=(now - timedelta(days=i)).isoformat(), message_id=i + 1)
            for i, t in enumerate(texts)
        ],
    )


class ClassifierV11V2Tests(unittest.TestCase):
    def test_disclaimer_is_not_positive_investment_evidence(self):
        row = dict(
            name="Автор",
            description="Экономика, политика, рынки.\nПосты канала - не инвестиционные рекомендации.\n16+",
        )
        cleaned = clean_profile(row)["description"]
        self.assertEqual(cleaned, "Экономика, политика, рынки.")
        self.assertIn(
            "Инвестиции",
            clean_profile(
                dict(name="Автор", description="Инвестиции в акции и облигации.")
            )["description"],
        )

    def test_bataysk_city_and_reading_verb_are_not_zabaykalye(self):
        self.assertIn(
            "region:Ростовская область", safe_mentions("ГИНЕКОЛОГИЯ РЖД БАТАЙСК")
        )
        self.assertIn(
            "region:Ростовская область", safe_mentions("г. Батайск, ул. Куйбышева, 140")
        )
        self.assertNotIn(
            "region:Забайкальский край", safe_mentions("Читаю — и ком в горле")
        )
        self.assertIn("region:Забайкальский край", safe_mentions("Чита"))
        self.assertNotIn(
            "region:Ростовская область", safe_mentions("забатайский вымышленный термин")
        )

    def test_local_clinic_has_region_even_without_news_words(self):
        row = dict(
            name="ГИНЕКОЛОГИЯ РЖД БАТАЙСК", description="ОТДЕЛЕНИЕ ГДЕ ПОМОГАЮТ!"
        )
        posts = [
            dict(text="Записаться на консультацию. г. Батайск, ул. Куйбышева, 140")
        ]
        result = geography(row, posts, "v11_medicine")
        self.assertEqual(result["geography_regions"], ["Ростовская область"])
        self.assertEqual(result["geography_scope"], "region")
        self.assertEqual(result["geography_places"][0]["label"], "Батайск")
        negative = geography(
            dict(
                name="Гинекология",
                description="Родилась в Батайске.\nОбсуждаем исследования и клинические рекомендации.",
            ),
            [],
            "v11_medicine",
        )
        self.assertEqual(negative["geography_scope"], "unknown")

    def test_coarse_head_can_resolve_section_when_fine_topics_tie(self):
        import numpy as np

        model = TopicModel(FakeEncoder())
        fine = np.zeros((4, len(model.topics)))
        fine[:, model.by_id["v11_accounting_taxes"]] = 1.4
        fine[:, model.by_id["v11_management"]] = 1.39
        fine[:, model.by_id["v11_personal_money"]] = 1.38
        coarse = np.zeros((4, len(model.groups)))
        coarse[:, model.group_ids.index("v11_business_work")] = 2.5
        with (
            patch.object(model, "scores", return_value=fine),
            patch.object(model, "coarse_scores", return_value=coarse),
        ):
            result = model.decide(
                dict(name="Автор", description="Разные профессиональные вопросы"),
                [dict(text="Обсуждение профессиональных вопросов")] * 3,
                np.zeros((4, 32)),
            )
        self.assertIsNone(result["subcategory"])
        self.assertEqual(result["topic_granularity"], "group")
        self.assertEqual(result["topic_group"], "v11_business_work")

    def test_missing_geography_reference_is_not_counted_as_correct(self):
        result = {
            "channels": {
                "demo": dict(
                    subcategory="v11_management",
                    topic_group="v11_business_work",
                    topic_granularity="topic",
                )
            }
        }
        labels = dict(
            split="development",
            cases=[
                dict(
                    username="demo",
                    topics=["v11_management"],
                    groups=["v11_business_work"],
                )
            ],
        )
        report = evaluate(result, labels)
        self.assertEqual(report["geography_evaluated"], 0)
        self.assertEqual(report["geography_correct"], 0)

    def test_concordant_topic_and_group_heads_preserve_supported_section(self):
        import numpy as np

        model = TopicModel(FakeEncoder())
        fine = np.zeros((4, len(model.topics)))
        fine[:, model.by_id["v11_management"]] = 1.4
        fine[:, model.by_id["v11_accounting_taxes"]] = 1.39
        fine[:, model.by_id["v11_investments"]] = 1.2
        coarse = np.zeros((4, len(model.groups)))
        coarse[:, model.group_ids.index("v11_finance")] = 0.27
        with (
            patch.object(model, "scores", return_value=fine),
            patch.object(model, "coarse_scores", return_value=coarse),
        ):
            result = model.decide(
                dict(name="Автор", description="Обсуждение профессиональных вопросов"),
                [dict(text="Обсуждение разных профессиональных вопросов")] * 3,
                np.zeros((4, 32)),
            )
        self.assertEqual(result["topic_granularity"], "group")
        self.assertEqual(result["topic_group"], "v11_business_work")
        self.assertTrue(result["topic_diagnostics"]["group_topic_head_agreement"])
        self.assertLess(
            result["topic_diagnostics"]["group_margin"],
            settings()["minimum_group_margin"],
        )

    def test_coarse_head_alone_cannot_classify_empty_profile(self):
        import numpy as np

        model = TopicModel(FakeEncoder())
        coarse = np.zeros((1, len(model.groups)))
        coarse[:, model.group_ids.index("v11_finance")] = 3
        with (
            patch.object(
                model, "scores", return_value=np.zeros((1, len(model.topics)))
            ),
            patch.object(model, "coarse_scores", return_value=coarse),
        ):
            result = model.decide(
                dict(name="Канал", description=""), [], np.zeros((1, 32))
            )
        self.assertEqual(result["topic_granularity"], "abstain")

    def test_cfo_author_role_does_not_override_travel_posts(self):
        import numpy as np

        model = TopicModel(FakeEncoder())
        fine = np.zeros((4, len(model.topics)))
        fine[:, model.by_id["v11_tourism"]] = 3
        posts = [
            dict(text="Путешествия, туристические маршруты, отели и экскурсии.")
        ] * 3
        with patch.object(model, "scores", return_value=fine):
            result = model.decide(
                dict(
                    name="Финансовый директор CFO",
                    description="Сообщество финансистов и бухгалтеров",
                ),
                posts,
                np.zeros((4, 32)),
            )
        self.assertEqual(result["subcategory"], "v11_tourism")
        self.assertEqual(result["topic_diagnostics"]["group_identity_evidence"], [])

    def test_profile_cleanup_keeps_subject_and_removes_registration(self):
        row = dict(
            name="Банк Пример",
            description="Новые банковские продукты и услуги.\nРеестр: https://gosuslugi.ru/id\nДля агентов: https://t.me/team",
        )
        result = clean_profile(row)
        self.assertIn("банковские продукты", result["description"])
        self.assertNotIn("Реестр", result["description"])
        self.assertNotIn("Для агентов", result["description"])

    def test_safe_places_not_pronouns_or_person_names(self):
        self.assertNotIn(
            "country:TG", safe_mentions("для того чтобы узнать подробности")
        )
        self.assertNotIn(
            "country:TG", safe_mentions("Исходя из того, что известно сейчас")
        )
        self.assertNotIn("country:WS", safe_mentions("В самой важной части сообщения"))
        self.assertIn("country:WS", safe_mentions("Новости Самоа"))
        self.assertIn("country:TG", safe_mentions("Новости в Того"))
        self.assertNotIn(
            "region:Владимирская область",
            safe_mentions("Президент Владимир Путин выступил"),
        )
        self.assertIn(
            "region:Владимирская область", safe_mentions("Новости Владимирской области")
        )
        self.assertNotIn("region:Москва", safe_mentions("Московская область"))
        self.assertIn("region:Приморский край", safe_mentions("Новости Владивостока"))

    def test_repeated_country_mentions_do_not_establish_coverage(self):
        row = dict(
            name="Военно-аналитический центр",
            description="Военные действия и международная аналитика",
        )
        posts = [
            dict(text="События в Украине, России и Китае", date="2026-11-20")
            for _ in range(8)
        ]
        result = geography(row, posts, "v11_military_affairs", True)
        self.assertEqual(result["geography_scope"], "global")
        self.assertEqual(result["geography_country"], "")
        result = geography(
            dict(name="Канал", description="Обзор событий"), posts, "v11_general_news"
        )
        self.assertEqual(result["geography_scope"], "unknown")

    def test_profile_purpose_region_not_country_of_author(self):
        row = dict(
            name="Типичный Долгопрудный",
            description="Сообщество жителей. Присылайте новости.",
        )
        result = geography(row, [], "v11_general_news")
        self.assertEqual(result["geography_regions"], ["Московская область"])
        self.assertEqual(result["geography_scope"], "region")
        result = geography(
            dict(name="Банковские продукты", description="Карты и переводы"),
            [],
            "v11_banking_payments",
            True,
        )
        self.assertEqual(result["geography_scope"], "nonlocal")
        self.assertEqual(result["geography_country"], "")
        result = geography(
            dict(
                name="Жизнь в Китае",
                description="Переезд и новости местного сообщества",
            ),
            [],
            "v11_relocation",
        )
        self.assertEqual(result["geography_scope"], "country")
        self.assertEqual(result["geography_country"], "CN")

    def test_biography_registration_not_region_purpose(self):
        result = geography(
            dict(
                name="Инвестиции",
                description="Родился во Владивостоке.\nРегистрация: Москва.\nКриптовалюты и инвестиции.",
            ),
            [],
            "v11_crypto_markets",
            True,
        )
        self.assertNotIn(result["geography_scope"], ("region", "country"))

    def test_post_filter_excludes_service_low_information_and_duplicates(self):
        good = "В городе открыли новый парк и восстановили дорожки. Жители обсудили благоустройство улиц и работу общественного транспорта."
        data = sample(
            [
                good,
                good.replace("парк", "сквер"),
                "Присылайте новости и фото в нашего бота, сообщение можно прислать анонимно. Для срочных вопросов у нас есть чат.",
                "Сегодня прекрасный день, доброй ночи, любимый город!",
            ]
        )
        original = copy.deepcopy(data)
        posts, excluded = select_posts(data, settings())
        self.assertEqual(len(posts), 1)
        self.assertEqual(
            {x["reason"] for x in excluded},
            {"near_duplicate", "service_message", "low_information"},
        )
        self.assertEqual(data, original)

    def test_clear_subject_identity_is_general_not_username_lookup(self):
        import numpy as np

        model = TopicModel(FakeEncoder())
        texts = ["Активности TON и NFT, криптовалютные кошельки и цифровые активы."] * 3
        posts = [dict(text=t) for t in texts]
        for username in ["unseen_one", "totally_different"]:
            row = dict(
                username=username,
                name="Цифровые активы",
                description="TON & Crypto. Помощь новичкам для входа в криптомир. NFT-подарки.",
            )
            with patch.object(
                model, "scores", return_value=np.zeros((4, len(model.topics)))
            ):
                result = model.decide(row, posts, np.zeros((4, 32)))
            self.assertEqual(result["subcategory"], "v11_crypto_markets")
            self.assertIn(
                "v11_crypto_markets", result["topic_diagnostics"]["identity_topics"]
            )

    def test_bank_owner_does_not_force_banking_on_travel_content(self):
        import numpy as np

        model = TopicModel(FakeEncoder())
        row = dict(
            name="Банк Пример",
            description="Путешествия: туристические маршруты и впечатления от поездок.",
        )
        posts = [
            dict(text="Путешествия, туристические маршруты, отели и экскурсии.")
        ] * 3
        values = np.zeros((4, len(model.topics)))
        values[:, model.by_id["v11_tourism"]] = 3
        with patch.object(model, "scores", return_value=values):
            result = model.decide(row, posts, np.zeros((4, 32)))
        self.assertEqual(result["subcategory"], "v11_tourism")
        self.assertNotIn(
            "v11_banking_payments", result["topic_diagnostics"]["identity_topics"]
        )

    def test_ties_abstain_instead_of_mandatory_narrow_label(self):
        import numpy as np

        model = TopicModel(FakeEncoder())
        with patch.object(
            model, "scores", return_value=np.zeros((1, len(model.topics)))
        ):
            result = model.decide(
                dict(name="Канал", description=""), [], np.zeros((1, 32))
            )
        self.assertIsNone(result["subcategory"])
        self.assertEqual(result["topic_granularity"], "abstain")

    def test_business_reporting_is_not_automatically_entrepreneurship(self):
        import numpy as np

        model = TopicModel(FakeEncoder())
        row = dict(
            name="Бизнес-инсайды",
            description="Корпоративные конфликты, компании и сделки.",
        )
        with patch.object(
            model, "scores", return_value=np.zeros((1, len(model.topics)))
        ):
            result = model.decide(row, [], np.zeros((1, 32)))
        self.assertIsNone(result["subcategory"])
        self.assertEqual(result["topic_group"], "v11_business_work")
        self.assertEqual(result["topic_granularity"], "group")

    def test_stale_banking_description_does_not_beat_consistent_travel_posts(self):
        import numpy as np

        model = TopicModel(FakeEncoder())
        row = dict(
            name="Банк Пример", description="Новые продукты банка и банковские услуги."
        )
        posts = [
            dict(
                text="Путешествия и туристические маршруты. Отели и экскурсии, впечатления от поездок."
            )
        ] * 4
        values = np.zeros((5, len(model.topics)))
        values[1:, model.by_id["v11_tourism"]] = 3
        with patch.object(model, "scores", return_value=values):
            result = model.decide(row, posts, np.zeros((5, 32)))
        self.assertIn(
            "v11_banking_payments", result["topic_diagnostics"]["identity_conflicts"]
        )
        self.assertNotIn(
            "v11_banking_payments", result["topic_diagnostics"]["identity_topics"]
        )
        self.assertEqual(result["subcategory"], "v11_tourism")

    def test_manual_answers_preserved(self):
        row = dict(
            username="demo",
            name="Канал",
            description="",
            topic_source="manual",
            subcategory="v11_cooking",
            topic_group="v11_home_hobbies",
            geography_source="manual",
            geography_scope="country",
            geography_country="CN",
            geography_regions=[],
        )
        original = copy.deepcopy(row)
        result = classify([row], [{}], FakeEncoder())["channels"]["demo"]
        self.assertEqual(result["subcategory"], "v11_cooking")
        self.assertEqual(result["geography_country"], "CN")
        self.assertEqual(result["topic_source"], "manual")
        self.assertEqual(row, original)

    def test_development_cannot_be_claimed_as_holdout(self):
        with self.assertRaises(ValueError):
            evaluate(
                {"channels": {}}, dict(split="holdout", cases=[dict(username="rybar")])
            )
        report = evaluate(
            {"channels": {}}, dict(split="development", cases=[dict(username="rybar")])
        )
        self.assertEqual(report["evaluated"], 0)
        self.assertEqual(report["missing"], ["rybar"])

    def test_v11_publication_rejects_v11v2(self):
        from src.classification_runtime import validate_results

        with self.assertRaises(ValueError):
            validate_results(dict(format="atlas-results-v11v2", channels={}))
