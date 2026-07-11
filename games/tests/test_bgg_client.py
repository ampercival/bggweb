import io
import zipfile
from unittest import mock

from django.test import SimpleTestCase

from games.services.bgg_client import BGGClient


class FakeResp:
    def __init__(self, content=b"", status_code=200, text=None):
        self.content = content
        self.status_code = status_code
        self.text = text if text is not None else content.decode("utf-8", "ignore")
        self.headers = {}

    def raise_for_status(self):
        return None


def _client():
    # No real network and no throttling delays in tests.
    c = BGGClient(throttle_sec=0, detail_throttle_sec=0)
    c._sleep = lambda *a, **k: None
    return c


def _ranks_zip(rows):
    buf = io.BytesIO()
    csv_text = "rank,id,name,average,usersrated,is_expansion\n" + "\n".join(rows) + "\n"
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("boardgames_ranks_2026-01-01.csv", csv_text)
    return buf.getvalue()


class RanksZipParsingTests(SimpleTestCase):
    def test_parses_ranked_rows(self):
        content = _ranks_zip([
            "1,13,Catan,7.5,5000,0",
            "2,9209,Ticket to Ride,7.4,4000,0",
            "0,999,Unranked,5.0,1,0",  # rank 0 -> skipped
        ])
        client = _client()
        with mock.patch.object(client, "_get", return_value=FakeResp(content=content)):
            result = client.fetch_top_games_ranks(0, zip_url="http://example/zip")

        self.assertEqual(set(result.keys()), {"13", "9209"})
        self.assertEqual(result["13"]["Game Title"], "Catan")
        self.assertEqual(result["13"]["BGG Rank"], 1)
        self.assertEqual(result["13"]["Average Rating"], 7.5)
        self.assertEqual(result["13"]["Number of Voters"], 5000)
        self.assertEqual(result["13"]["Type"], "Base Game")

    def test_n_limit_truncates(self):
        content = _ranks_zip([f"{i},{i},G{i},7.0,100,0" for i in range(1, 11)])
        client = _client()
        with mock.patch.object(client, "_get", return_value=FakeResp(content=content)):
            result = client.fetch_top_games_ranks(3, zip_url="http://example/zip")
        self.assertEqual(len(result), 3)

    def test_expansion_flag(self):
        content = _ranks_zip(["1,5,Exp,7.0,100,1"])
        client = _client()
        with mock.patch.object(client, "_get", return_value=FakeResp(content=content)):
            result = client.fetch_top_games_ranks(0, zip_url="http://example/zip")
        self.assertEqual(result["5"]["Type"], "Expansion")

    def test_missing_zip_url_raises(self):
        with self.assertRaises(RuntimeError):
            _client().fetch_top_games_ranks(10, zip_url=None)


THING_XML = b"""<?xml version="1.0"?>
<items>
  <item id="13">
    <yearpublished value="1995"/>
    <statistics>
      <ratings>
        <averageweight value="2.34"/>
        <numweights value="100"/>
        <ranks>
          <rank type="subtype" name="boardgame" value="1"/>
          <rank type="family" name="strategygames" value="5"/>
        </ranks>
      </ratings>
    </statistics>
    <link type="boardgamecategory" value="Economic"/>
    <link type="boardgamecategory" value="Negotiation"/>
    <link type="boardgamemechanic" value="Trading"/>
    <poll name="suggested_numplayers">
      <results numplayers="3">
        <result value="Best" numvotes="50"/>
        <result value="Recommended" numvotes="30"/>
        <result value="Not Recommended" numvotes="20"/>
      </results>
      <results numplayers="4+">
        <result value="Best" numvotes="1"/>
      </results>
    </poll>
  </item>
</items>"""


class ThingDetailParsingTests(SimpleTestCase):
    def test_parses_details_and_player_counts(self):
        client = _client()
        with mock.patch.object(client, "_get", return_value=FakeResp(content=THING_XML)):
            details, counts = client.fetch_details_batches(["13"], batch_size=20)

        d = details["13"]
        self.assertEqual(d["Year"], "1995")
        self.assertEqual(d["Weight"], 2.34)
        self.assertEqual(d["Weight Votes"], 100)
        self.assertEqual(d["BGG Rank"], 1)
        self.assertEqual(set(d["Categories"]), {"Economic", "Negotiation"})
        self.assertEqual(d["Mechanics"], ["Trading"])
        self.assertEqual(d["Families"], ["Strategy"])

        pc = counts["13"]
        self.assertIn("3", pc)
        self.assertNotIn("4+", pc)  # '+' results are skipped
        self.assertEqual(pc["3"]["Best %"], 50.0)
        self.assertEqual(pc["3"]["Rec. %"], 30.0)
        self.assertEqual(pc["3"]["Not %"], 20.0)
        self.assertEqual(pc["3"]["Total Votes"], 100)


FAMILY_XML = b"""<?xml version="1.0"?>
<items>
  <item type="boardgamefamily" id="70360">
    <name type="primary" sortindex="1" value="Digital Implementations: Board Game Arena"/>
    <description>Games playable on Board Game Arena.</description>
    <link type="boardgamefamily" id="13" value="Catan" inbound="true"/>
    <link type="boardgamefamily" id="9209" value="Ticket to Ride" inbound="true"/>
    <link type="boardgamefamily" id="13" value="Catan (dup)" inbound="true"/>
    <link type="boardgamecategory" id="1021" value="Economic"/>
  </item>
</items>"""


class FamilyParsingTests(SimpleTestCase):
    def test_parses_inbound_member_games(self):
        client = _client()
        with mock.patch.object(client, "_get", return_value=FakeResp(content=FAMILY_XML)):
            members = client.fetch_family_members("70360")
        # Inbound links only, de-duplicated, non-inbound links ignored.
        self.assertEqual([m["bgg_id"] for m in members], ["13", "9209"])
        self.assertEqual(members[0]["title"], "Catan")

    def test_empty_family_raises(self):
        client = _client()
        empty = b"<items><item type='boardgamefamily' id='1'></item></items>"
        with mock.patch.object(client, "_get", return_value=FakeResp(content=empty)):
            with self.assertRaises(RuntimeError):
                client.fetch_family_members("1")


COLLECTION_XML = b"""<?xml version="1.0"?>
<items>
  <item objectid="13">
    <name>Catan</name>
    <stats>
      <average value="7.5"/>
      <usersrated value="5000"/>
    </stats>
  </item>
</items>"""


class CollectionParsingTests(SimpleTestCase):
    def test_parses_owned_collection(self):
        client = _client()
        with mock.patch.object(client.session, "get",
                               return_value=FakeResp(content=COLLECTION_XML)):
            owned = client.fetch_owned_collection("alice")

        self.assertIn("13", owned)
        self.assertEqual(owned["13"]["Game Title"], "Catan")
        self.assertEqual(owned["13"]["Owned"], "Owned")
        self.assertEqual(owned["13"]["Average Rating"], 7.5)
        self.assertEqual(owned["13"]["Number of Voters"], 5000)
