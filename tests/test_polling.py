import threading
import time
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from src import pipeline, scraper, db
from src.models import Channel, Post

def posts(*ids):
    return [Post(0,i,f'https://t.me/test/{i}') for i in ids]

class PollingTests(unittest.TestCase):
    def fetch(self,pages,**kwargs):
        with patch.object(pipeline,'fetch_channel_html',return_value='html') as network, patch.object(pipeline,'parse_channel_page',side_effect=pages):
            result=pipeline._fetch_pages(Channel('test'),**kwargs)
            return result,network

    def test_incremental_stops_at_saved_id(self):
        (_,batches,state),network=self.fetch([posts(105,104,100,99)],last_seen=100,max_pages=5)
        self.assertEqual(network.call_count,1)
        self.assertEqual([p.tg_message_id for p in batches[0]],[105,104])
        self.assertIsNone(state)

    def test_unchanged_channel_is_one_request(self):
        (_,batches,state),network=self.fetch([posts(100,99)],last_seen=100,max_pages=5)
        self.assertEqual(network.call_count,1)
        self.assertEqual(batches,[[]])

    def test_gap_checkpoint_is_resumed_without_losing_older_delta(self):
        (_,_,state),_=self.fetch([posts(202,201)],last_seen=100,max_pages=1)
        self.assertEqual(state,(201,100))
        (_,batches,state),network=self.fetch([posts(205,204),posts(203,202,201),posts(200,199,100)],last_seen=202,max_pages=3,checkpoint=state)
        self.assertEqual([c.kwargs['before'] for c in network.call_args_list],[None,204,201])
        self.assertEqual([p.tg_message_id for batch in batches for p in batch],[205,204,203,200,199])
        self.assertIsNone(state)

    def test_cursor_ignored_does_not_loop_or_clear_gap(self):
        (_,_,state),network=self.fetch([posts(110,109),posts(110,109)],last_seen=100,max_pages=10)
        self.assertEqual(network.call_count,2)
        self.assertEqual(state,(109,100))

    def test_full_history_keeps_saved_posts_and_existing_checkpoint(self):
        (_,batches,state),_=self.fetch([posts(100,99)],last_seen=100,max_pages=1,full_history=True)
        self.assertEqual(len(batches[0]),2)
        self.assertIs(state,False)

    def test_bounded_parallel_fetch_and_serial_writer(self):
        lock=threading.Lock();active=0;peak=0;main=threading.get_ident()
        def fetch(*args):
            nonlocal active,peak
            with lock:active+=1;peak=max(peak,active)
            time.sleep(.03)
            with lock:active-=1
            return 'html',[],None
        def store(*args):
            self.assertEqual(threading.get_ident(),main)
            return 0,0
        rows=[dict(id=i,username=f'c{i}',name=f'c{i}') for i in range(8)]
        with patch.object(pipeline,'connect',return_value=MagicMock()),patch.object(pipeline,'load_channels',return_value=[]),patch.object(pipeline,'transaction',side_effect=lambda c:nullcontext()),patch.object(pipeline,'get_active_channels',return_value=rows),patch.object(pipeline,'get_post_watermarks',return_value={}),patch.object(pipeline,'get_scrape_checkpoints',return_value={}),patch.object(pipeline,'_fetch_pages',side_effect=fetch),patch.object(pipeline,'_store_channel',side_effect=store):
            pipeline.run(workers=3)
        self.assertGreater(peak,1);self.assertLessEqual(peak,3)

    def test_database_failure_rolls_back_posts_and_does_not_advance_cursor(self):
        conn=MagicMock()
        with patch.object(pipeline,'upsert_channel',return_value=1),patch.object(pipeline,'insert_posts',side_effect=RuntimeError('write failed')),patch.object(pipeline,'save_scrape_checkpoint') as checkpoint:
            with self.assertRaises(RuntimeError):
                pipeline._store_channel(conn,Channel('test'),'test',None,[posts(2)],(2,1))
            checkpoint.assert_not_called()
            conn.rollback.assert_called_once()
            conn.commit.assert_not_called()

    def test_bulk_insert_keeps_database_deduplication(self):
        with patch.object(db,'_cursor',side_effect=lambda c:nullcontext(MagicMock())),patch('psycopg2.extras.execute_values',return_value=[{'id':1}]) as bulk:
            self.assertEqual(db.insert_posts(None,posts(1,2)),1)
            self.assertIn('DO NOTHING',bulk.call_args.args[1])
            self.assertEqual(len(bulk.call_args.args[2]),2)

class RateLimitTests(unittest.TestCase):
    def test_shared_cooldown_and_spacing(self):
        now=[100.]
        with patch.object(scraper.time,'monotonic',side_effect=lambda:now[0]),patch.object(scraper.time,'sleep',side_effect=lambda n:now.__setitem__(0,now[0]+n)):
            gate=scraper.RequestGate();gate.wait();gate.pause(300);gate.wait()
            self.assertGreaterEqual(now[0],400)

    def test_workers_have_independent_reusable_sessions(self):
        from concurrent.futures import ThreadPoolExecutor
        barrier=threading.Barrier(2)
        def worker():
            a=scraper._get_session();barrier.wait();return id(a), id(scraper._get_session())
        with patch.object(scraper,'_local',threading.local()):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _:worker(),range(2)))
        self.assertNotEqual(results[0][0],results[1][0])
        self.assertTrue(all(a==b for a,b in results))

    def test_retry_after_not_shortened(self):
        self.assertEqual(scraper._retry_after('300'),300)
        self.assertIsNone(scraper._retry_after('invalid'))
        response=MagicMock(status_code=429,headers={'Retry-After':'300'})
        good=MagicMock(status_code=200,text='ok')
        with patch.object(scraper,'_get_session',return_value=MagicMock(get=MagicMock(side_effect=[response,good]))),patch.object(scraper,'_gate') as gate:
            self.assertEqual(scraper.fetch_channel_html('test'),'ok')
            gate.pause.assert_called_with(300)
            self.assertEqual(gate.wait.call_count,2)
