import unittest
from unittest.mock import MagicMock, patch
from contextlib import nullcontext
from src import public_metadata, scraper
from src.parser import parse_channel_meta

PROFILE='''<div class="tgme_page_title"><span>Тестовый канал</span></div><div class="tgme_page_description">Описание<br>канала</div><div class="tgme_page_extra">12 345 subscribers</div>'''
class PublicMetadataTests(unittest.TestCase):
    def test_profile_fields(self):
        meta=parse_channel_meta(PROFILE)
        self.assertEqual(meta['name'],'Тестовый канал')
        self.assertEqual(meta['subscribers'],12345)
        self.assertIn('Описание',meta['description'])

    def test_rounded_counter_and_empty_description(self):
        meta=parse_channel_meta('<div class="tgme_page_title">Канал</div><div class="tgme_page_extra">1.2M subscribers</div>')
        self.assertEqual(meta['subscribers'],1200000)
        self.assertEqual(meta['description'],'')

    def test_personal_profile_is_not_a_channel(self):
        with patch.object(public_metadata,'fetch_channel_html',return_value='<div class="tgme_page_title">Person</div><div class="tgme_page_extra">@person</div>'):
            with self.assertRaises(scraper.ScrapeError):public_metadata.fetch_metadata('test')

    def test_profile_url_does_not_request_publication_feed(self):
        session=MagicMock();session.get.return_value=MagicMock(status_code=200,text=PROFILE)
        with patch.object(scraper,'_get_session',return_value=session),patch.object(scraper,'_gate'):
            scraper.fetch_channel_html('test',profile=True)
        self.assertEqual(session.get.call_args.args[0],'https://t.me/test')
        self.assertNotIn('/s/',session.get.call_args.args[0])

    def test_updates_metadata_without_touching_posts_or_polling_state(self):
        conn=MagicMock()
        with patch.object(public_metadata.db,'connect',return_value=conn),patch.object(public_metadata.db,'get_active_channels',return_value=[{'id':1,'username':'test'}]),patch.object(public_metadata,'fetch_metadata',return_value={'name':'New','description':'','subscribers':123}),patch.object(public_metadata.db,'update_channel_meta') as update,patch.object(public_metadata.db,'insert_posts') as posts,patch.object(public_metadata.db,'save_scrape_checkpoint') as checkpoint,patch.object(public_metadata.db,'mark_parsed') as mark:
            self.assertEqual(public_metadata.run(workers=1)['updated'],1)
            update.assert_called_once_with(conn,1,name='New',description='',subscribers=123)
            posts.assert_not_called();checkpoint.assert_not_called();mark.assert_not_called()

    def test_failure_does_not_zero_old_metadata(self):
        with patch.object(public_metadata.db,'connect',return_value=MagicMock()),patch.object(public_metadata.db,'get_active_channels',return_value=[{'id':1,'username':'test'}]),patch.object(public_metadata,'fetch_metadata',side_effect=RuntimeError('unavailable')),patch.object(public_metadata.db,'update_channel_meta') as update:
            self.assertEqual(public_metadata.run(workers=1)['failed'],1)
            update.assert_not_called()
