// ILLUSTRATIVE ARCHIVE: placeholder history for the time bar until the GDELT backfill exists.
//
// The events are real, with their real dates and places. The coverage numbers are made up: each
// event is a few bursts of attention that decay, plus a steady base for long-running stories. Any
// day the API has a real snapshot for (meta.dates) uses the real data instead (timeline/source.ts),
// so this file can simply be deleted once the backfill covers the range.
//
// Everything here is pure and computed on demand: a day's snapshot is ~100 events x 14 days of
// arithmetic, cheap enough to recompute on every step of a drag.
import type { Item, Location, Snapshot, StoryDetail, StorySummary } from '../api/types.gen'
import type { Category } from '../globe/categories'
import { toDay, toIndex, type DayIndex } from './days'

interface Place {
  name: string
  country: string | null
  lat: number
  lon: number
}

interface HistoryEvent {
  id: string
  title: string
  category: Category
  /** Main place: the marker sits here. */
  place: Place
  /** Other places in the story's footprint. */
  also?: Place[]
  /** Wikipedia search term for the event's article. */
  wiki: string
  /** Bursts of coverage: [day, peak articles per day]. The first one is when the story starts. */
  bursts: [string, number][]
  /** How long each burst takes to fade (e-folding time, days). */
  decayDays?: number
  /** Steady articles per day from the first burst until `end`, for long-running stories. */
  ongoing?: number
  /** No coverage after this day. */
  end?: string
  /** Spread of the heat around each place, km. */
  radiusKm?: number
}

const p = (name: string, country: string | null, lat: number, lon: number): Place => ({
  name,
  country,
  lat,
  lon,
})

const KYIV = p('Kyiv', 'UA', 50.45, 30.52)
const MOSCOW = p('Moscow', 'RU', 55.76, 37.62)
const LONDON = p('London', 'GB', 51.51, -0.13)
const PARIS = p('Paris', 'FR', 48.86, 2.35)
const WASHINGTON = p('Washington, D.C.', 'US', 38.9, -77.04)
const SAN_FRANCISCO = p('San Francisco', 'US', 37.77, -122.42)
const GENEVA = p('Geneva', 'CH', 46.2, 6.14)
const TAIPEI = p('Taipei', 'TW', 25.03, 121.56)
const BRASILIA = p('Brasília', 'BR', -15.79, -47.88)
const TEHRAN = p('Tehran', 'IR', 35.69, 51.39)
const TEL_AVIV = p('Tel Aviv', 'IL', 32.08, 34.78)
const NEW_DELHI = p('New Delhi', 'IN', 28.61, 77.21)
const ALEPPO = p('Aleppo', 'SY', 36.2, 37.13)
const MEXICO_CITY = p('Mexico City', 'MX', 19.43, -99.13)

const EVENTS: HistoryEvent[] = [
  // 2021
  {
    id: 'hist-omicron',
    title: 'WHO names new Covid-19 variant Omicron',
    category: 'health',
    place: p('Johannesburg', 'ZA', -26.2, 28.05),
    also: [GENEVA],
    wiki: 'SARS-CoV-2 Omicron variant',
    bursts: [
      ['2021-11-26', 1800],
      ['2021-12-15', 700],
    ],
    decayDays: 10,
  },
  {
    id: 'hist-ukraine-buildup',
    title: "Russian troop build-up near Ukraine's border",
    category: 'conflict',
    place: KYIV,
    also: [MOSCOW],
    wiki: 'Prelude to the Russian invasion of Ukraine',
    bursts: [
      ['2021-11-12', 300],
      ['2021-12-07', 500],
      ['2022-01-10', 600],
      ['2022-02-15', 900],
      ['2022-02-21', 1500],
    ],
    ongoing: 120,
    end: '2022-02-23',
  },
  {
    id: 'hist-kentucky-tornadoes',
    title: 'Tornado outbreak devastates Mayfield, Kentucky',
    category: 'disaster',
    place: p('Mayfield, Kentucky', 'US', 36.74, -88.64),
    wiki: 'Tornado outbreak of December 10–11, 2021',
    bursts: [['2021-12-11', 1200]],
    decayDays: 5,
  },
  {
    id: 'hist-chile-boric',
    title: "Gabriel Boric wins Chile's presidential run-off",
    category: 'politics',
    place: p('Santiago', 'CL', -33.45, -70.67),
    wiki: '2021 Chilean presidential election',
    bursts: [['2021-12-20', 500]],
  },
  // 2022
  {
    id: 'hist-kazakhstan-unrest',
    title: 'Fuel-price protests turn deadly in Kazakhstan',
    category: 'society',
    place: p('Almaty', 'KZ', 43.24, 76.89),
    wiki: '2022 Kazakh unrest',
    bursts: [
      ['2022-01-03', 300],
      ['2022-01-06', 1000],
    ],
  },
  {
    id: 'hist-tonga-eruption',
    title: 'Hunga Tonga volcano erupts; tsunami hits Tonga',
    category: 'disaster',
    place: p('Nukuʻalofa', 'TO', -21.14, -175.2),
    wiki: '2022 Hunga Tonga–Hunga Haʻapai eruption and tsunami',
    bursts: [['2022-01-15', 900]],
  },
  {
    id: 'hist-beijing-olympics',
    title: 'Beijing Winter Olympics open',
    category: 'other',
    place: p('Beijing', 'CN', 39.9, 116.41),
    wiki: '2022 Winter Olympics',
    bursts: [
      ['2022-02-04', 700],
      ['2022-02-20', 300],
    ],
    decayDays: 8,
  },
  {
    id: 'hist-ukraine-war',
    title: 'Russia invades Ukraine',
    category: 'conflict',
    place: KYIV,
    also: [p('Kharkiv', 'UA', 49.99, 36.23), p('Mariupol', 'UA', 47.1, 37.55), MOSCOW],
    wiki: 'Russian invasion of Ukraine',
    bursts: [
      ['2022-02-24', 5000],
      ['2022-04-03', 1800],
      ['2022-09-11', 1200],
      ['2022-11-11', 900],
      ['2023-06-06', 1300],
      ['2024-08-07', 1000],
      ['2025-02-28', 1500],
      ['2025-08-15', 1300],
    ],
    decayDays: 10,
    ongoing: 350,
    radiusKm: 300,
  },
  {
    id: 'hist-macron',
    title: 'Macron re-elected French president',
    category: 'politics',
    place: PARIS,
    wiki: '2022 French presidential election',
    bursts: [['2022-04-25', 700]],
  },
  {
    id: 'hist-uvalde',
    title: 'Gunman kills 21 at Uvalde elementary school',
    category: 'society',
    place: p('Uvalde, Texas', 'US', 29.21, -99.79),
    wiki: 'Uvalde school shooting',
    bursts: [['2022-05-25', 1500]],
  },
  {
    id: 'hist-abe',
    title: 'Shinzo Abe assassinated in Nara',
    category: 'politics',
    place: p('Nara', 'JP', 34.69, 135.8),
    wiki: 'Assassination of Shinzo Abe',
    bursts: [['2022-07-08', 1600]],
  },
  {
    id: 'hist-sri-lanka',
    title: "Protesters storm Sri Lanka's presidential residence",
    category: 'society',
    place: p('Colombo', 'LK', 6.93, 79.85),
    wiki: '2022 Sri Lankan protests',
    bursts: [
      ['2022-07-09', 900],
      ['2022-07-13', 700],
    ],
  },
  {
    id: 'hist-pelosi-taiwan',
    title: 'Pelosi visits Taiwan; China stages military drills',
    category: 'politics',
    place: TAIPEI,
    wiki: '2022 visit by Nancy Pelosi to Taiwan',
    bursts: [['2022-08-02', 1200]],
  },
  {
    id: 'hist-pakistan-floods',
    title: 'Record floods submerge large parts of Pakistan',
    category: 'disaster',
    place: p('Sindh', 'PK', 26.0, 68.5),
    wiki: '2022 Pakistan floods',
    bursts: [
      ['2022-08-26', 900],
      ['2022-09-01', 600],
    ],
    decayDays: 12,
    radiusKm: 250,
  },
  {
    id: 'hist-queen',
    title: 'Queen Elizabeth II dies at Balmoral',
    category: 'other',
    place: LONDON,
    also: [p('Balmoral', 'GB', 57.04, -3.23)],
    wiki: 'Death and state funeral of Elizabeth II',
    bursts: [
      ['2022-09-08', 4000],
      ['2022-09-19', 1800],
    ],
    decayDays: 5,
  },
  {
    id: 'hist-iran-protests',
    title: "Protests spread across Iran after Mahsa Amini's death",
    category: 'society',
    place: TEHRAN,
    wiki: 'Mahsa Amini protests',
    bursts: [
      ['2022-09-17', 600],
      ['2022-09-22', 1100],
      ['2022-10-08', 500],
    ],
    decayDays: 10,
    ongoing: 60,
    end: '2023-01-31',
  },
  {
    id: 'hist-nord-stream',
    title: 'Explosions rupture the Nord Stream gas pipelines',
    category: 'conflict',
    place: p('Baltic Sea, off Bornholm', null, 55.3, 15.6),
    wiki: 'Nord Stream pipelines sabotage',
    bursts: [['2022-09-27', 1000]],
  },
  {
    id: 'hist-truss',
    title: 'Liz Truss resigns as UK prime minister',
    category: 'politics',
    place: LONDON,
    wiki: 'Premiership of Liz Truss',
    bursts: [
      ['2022-10-20', 1300],
      ['2022-10-25', 700],
    ],
  },
  {
    id: 'hist-lula',
    title: "Lula wins Brazil's presidential run-off",
    category: 'politics',
    place: BRASILIA,
    wiki: '2022 Brazilian general election',
    bursts: [['2022-10-31', 900]],
  },
  {
    id: 'hist-ftx',
    title: 'Crypto exchange FTX files for bankruptcy',
    category: 'economy',
    place: p('Nassau', 'BS', 25.05, -77.35),
    wiki: 'Bankruptcy of FTX',
    bursts: [
      ['2022-11-09', 700],
      ['2022-11-11', 1100],
      ['2022-12-13', 600],
    ],
  },
  {
    id: 'hist-world-cup-2022',
    title: 'World Cup kicks off in Qatar',
    category: 'other',
    place: p('Doha', 'QA', 25.29, 51.53),
    wiki: '2022 FIFA World Cup',
    bursts: [
      ['2022-11-20', 1200],
      ['2022-12-18', 1500],
    ],
    decayDays: 8,
  },
  {
    id: 'hist-chatgpt',
    title: 'OpenAI releases ChatGPT',
    category: 'technology',
    place: SAN_FRANCISCO,
    wiki: 'ChatGPT',
    bursts: [
      ['2022-12-01', 300],
      ['2023-01-23', 500],
      ['2023-03-14', 600],
    ],
    ongoing: 40,
    end: '2023-06-30',
  },
  // 2023
  {
    id: 'hist-brasilia-riots',
    title: "Bolsonaro supporters storm Brazil's Congress",
    category: 'society',
    place: BRASILIA,
    wiki: '2023 Brazilian Congress attack',
    bursts: [['2023-01-08', 1300]],
  },
  {
    id: 'hist-east-palestine',
    title: 'Derailed train spills toxic chemicals in East Palestine, Ohio',
    category: 'disaster',
    place: p('East Palestine, Ohio', 'US', 40.83, -80.54),
    wiki: 'East Palestine, Ohio, train derailment',
    bursts: [
      ['2023-02-04', 300],
      ['2023-02-14', 700],
    ],
  },
  {
    id: 'hist-turkey-quake',
    title: 'Earthquakes devastate southern Turkey and northern Syria',
    category: 'disaster',
    place: p('Kahramanmaraş', 'TR', 37.58, 36.94),
    also: [p('Antakya', 'TR', 36.2, 36.16), p('Gaziantep', 'TR', 37.07, 37.38), ALEPPO],
    wiki: '2023 Turkey–Syria earthquakes',
    bursts: [['2023-02-06', 4500]],
    decayDays: 9,
    radiusKm: 250,
  },
  {
    id: 'hist-svb',
    title: 'Silicon Valley Bank collapses',
    category: 'economy',
    place: p('Santa Clara', 'US', 37.35, -121.96),
    wiki: 'Collapse of Silicon Valley Bank',
    bursts: [
      ['2023-03-10', 1500],
      ['2023-03-13', 1000],
    ],
  },
  {
    id: 'hist-credit-suisse',
    title: 'UBS agrees emergency takeover of Credit Suisse',
    category: 'economy',
    place: p('Zurich', 'CH', 47.37, 8.54),
    wiki: 'Acquisition of Credit Suisse by UBS',
    bursts: [
      ['2023-03-15', 600],
      ['2023-03-19', 1300],
    ],
  },
  {
    id: 'hist-sudan-war',
    title: "War breaks out between Sudan's army and the RSF",
    category: 'conflict',
    place: p('Khartoum', 'SD', 15.5, 32.56),
    also: [p('El Fasher', 'SD', 13.63, 25.35)],
    wiki: 'Sudanese civil war (2023–present)',
    bursts: [
      ['2023-04-15', 1600],
      ['2025-10-27', 1100],
    ],
    decayDays: 10,
    ongoing: 70,
    radiusKm: 300,
  },
  {
    id: 'hist-coronation',
    title: 'Coronation of King Charles III',
    category: 'other',
    place: LONDON,
    wiki: 'Coronation of Charles III and Camilla',
    bursts: [['2023-05-06', 1500]],
    decayDays: 4,
  },
  {
    id: 'hist-erdogan',
    title: "Erdoğan wins Turkey's presidential run-off",
    category: 'politics',
    place: p('Ankara', 'TR', 39.93, 32.86),
    wiki: '2023 Turkish presidential election',
    bursts: [
      ['2023-05-15', 500],
      ['2023-05-29', 800],
    ],
  },
  {
    id: 'hist-titan',
    title: 'Titan submersible lost on a dive to the Titanic',
    category: 'other',
    place: p('North Atlantic', null, 41.73, -49.95),
    wiki: 'Titan submersible implosion',
    bursts: [
      ['2023-06-20', 900],
      ['2023-06-22', 1200],
    ],
  },
  {
    id: 'hist-wagner',
    title: 'Wagner Group mutiny marches toward Moscow',
    category: 'conflict',
    place: p('Rostov-on-Don', 'RU', 47.24, 39.71),
    also: [MOSCOW],
    wiki: 'Wagner Group rebellion',
    bursts: [
      ['2023-06-24', 2000],
      ['2023-08-24', 1300],
    ],
    decayDays: 5,
  },
  {
    id: 'hist-niger-coup',
    title: 'Military seizes power in Niger',
    category: 'politics',
    place: p('Niamey', 'NE', 13.51, 2.11),
    wiki: "2023 Nigerien coup d'état",
    bursts: [['2023-07-27', 600]],
    decayDays: 10,
  },
  {
    id: 'hist-maui',
    title: 'Wildfire destroys Lahaina on Maui',
    category: 'disaster',
    place: p('Lahaina, Hawaii', 'US', 20.88, -156.68),
    wiki: '2023 Hawaii wildfires',
    bursts: [['2023-08-09', 1200]],
  },
  {
    id: 'hist-chandrayaan',
    title: "India's Chandrayaan-3 lands near the Moon's south pole",
    category: 'technology',
    place: p('Bengaluru', 'IN', 12.97, 77.59),
    wiki: 'Chandrayaan-3',
    bursts: [['2023-08-23', 1000]],
    decayDays: 4,
  },
  {
    id: 'hist-morocco-quake',
    title: "Earthquake strikes Morocco's High Atlas",
    category: 'disaster',
    place: p('Al Haouz', 'MA', 31.06, -8.39),
    also: [p('Marrakesh', 'MA', 31.63, -8.01)],
    wiki: '2023 Marrakesh–Safi earthquake',
    bursts: [['2023-09-09', 1600]],
  },
  {
    id: 'hist-derna',
    title: 'Dam collapses flood Derna, Libya',
    category: 'disaster',
    place: p('Derna', 'LY', 32.76, 22.64),
    wiki: '2023 Derna dam collapses',
    bursts: [['2023-09-12', 1300]],
  },
  {
    id: 'hist-gaza-war',
    title: 'Hamas attacks Israel; war in Gaza',
    category: 'conflict',
    place: p('Gaza City', 'PS', 31.5, 34.47),
    also: [p('Rafah', 'PS', 31.3, 34.25), TEL_AVIV],
    wiki: 'Gaza war',
    bursts: [
      ['2023-10-07', 5000],
      ['2023-10-17', 2200],
      ['2023-10-28', 1500],
      ['2023-11-24', 1500],
      ['2024-05-07', 1200],
      ['2025-01-19', 1400],
      ['2025-03-18', 1100],
      ['2025-10-10', 1700],
    ],
    decayDays: 10,
    ongoing: 400,
  },
  {
    id: 'hist-altman',
    title: 'OpenAI board ousts Sam Altman',
    category: 'technology',
    place: SAN_FRANCISCO,
    wiki: 'Removal of Sam Altman from OpenAI',
    bursts: [
      ['2023-11-18', 800],
      ['2023-11-22', 600],
    ],
    decayDays: 3,
  },
  {
    id: 'hist-milei',
    title: "Javier Milei wins Argentina's presidency",
    category: 'politics',
    place: p('Buenos Aires', 'AR', -34.6, -58.38),
    wiki: '2023 Argentine general election',
    bursts: [['2023-11-20', 800]],
  },
  // 2024
  {
    id: 'hist-noto',
    title: "Earthquake hits Japan's Noto Peninsula",
    category: 'disaster',
    place: p('Wajima', 'JP', 37.39, 136.9),
    wiki: '2024 Noto earthquake',
    bursts: [['2024-01-01', 1300]],
  },
  {
    id: 'hist-taiwan-election',
    title: "Lai Ching-te wins Taiwan's presidential election",
    category: 'politics',
    place: TAIPEI,
    wiki: '2024 Taiwanese presidential election',
    bursts: [['2024-01-13', 700]],
  },
  {
    id: 'hist-navalny',
    title: 'Alexei Navalny dies in an Arctic prison',
    category: 'politics',
    place: p('Kharp', 'RU', 66.8, 65.81),
    also: [MOSCOW],
    wiki: 'Death of Alexei Navalny',
    bursts: [
      ['2024-02-16', 1600],
      ['2024-03-01', 600],
    ],
  },
  {
    id: 'hist-haiti',
    title: "Gangs overrun Port-au-Prince; Haiti's prime minister resigns",
    category: 'conflict',
    place: p('Port-au-Prince', 'HT', 18.59, -72.31),
    wiki: 'Haitian crisis (2018–present)',
    bursts: [
      ['2024-03-04', 500],
      ['2024-03-12', 800],
    ],
    decayDays: 10,
  },
  {
    id: 'hist-crocus',
    title: 'Gunmen attack Crocus City Hall near Moscow',
    category: 'conflict',
    place: p('Krasnogorsk', 'RU', 55.83, 37.39),
    wiki: 'Crocus City Hall attack',
    bursts: [['2024-03-23', 1800]],
  },
  {
    id: 'hist-key-bridge',
    title: "Ship strike collapses Baltimore's Key Bridge",
    category: 'disaster',
    place: p('Baltimore', 'US', 39.22, -76.53),
    wiki: 'Francis Scott Key Bridge collapse',
    bursts: [['2024-03-26', 1500]],
  },
  {
    id: 'hist-iran-israel-2024',
    title: 'Iran and Israel exchange direct strikes',
    category: 'conflict',
    place: p('Jerusalem', null, 31.77, 35.21),
    also: [TEHRAN],
    wiki: 'April 2024 Iranian strikes on Israel',
    bursts: [
      ['2024-04-14', 2200],
      ['2024-10-02', 1800],
      ['2024-10-26', 900],
    ],
    decayDays: 5,
  },
  {
    id: 'hist-raisi',
    title: "Iran's President Raisi killed in a helicopter crash",
    category: 'politics',
    place: p('Varzaqan', 'IR', 38.51, 46.65),
    also: [TEHRAN],
    wiki: '2024 Varzaqan helicopter crash',
    bursts: [['2024-05-20', 1500]],
  },
  {
    id: 'hist-sheinbaum',
    title: "Claudia Sheinbaum elected Mexico's first woman president",
    category: 'politics',
    place: MEXICO_CITY,
    wiki: '2024 Mexican general election',
    bursts: [['2024-06-03', 700]],
  },
  {
    id: 'hist-india-election',
    title: 'Modi wins a third term as India counts its votes',
    category: 'politics',
    place: NEW_DELHI,
    wiki: '2024 Indian general election',
    bursts: [['2024-06-04', 1300]],
  },
  {
    id: 'hist-uk-election',
    title: 'Labour wins UK general election by a landslide',
    category: 'politics',
    place: LONDON,
    wiki: '2024 United Kingdom general election',
    bursts: [['2024-07-05', 1600]],
  },
  {
    id: 'hist-butler',
    title: 'Trump wounded in assassination attempt in Butler, Pennsylvania',
    category: 'politics',
    place: p('Butler, Pennsylvania', 'US', 40.86, -79.9),
    wiki: 'Attempted assassination of Donald Trump in Pennsylvania',
    bursts: [['2024-07-14', 3500]],
    decayDays: 4,
  },
  {
    id: 'hist-crowdstrike',
    title: 'Faulty CrowdStrike update crashes Windows computers worldwide',
    category: 'technology',
    place: p('Austin, Texas', 'US', 30.27, -97.74),
    also: [LONDON, p('Sydney', 'AU', -33.87, 151.21)],
    wiki: '2024 CrowdStrike-related IT outages',
    bursts: [['2024-07-19', 2500]],
    decayDays: 3,
    radiusKm: 300,
  },
  {
    id: 'hist-biden-withdraws',
    title: 'Biden ends his re-election campaign; Harris steps in',
    category: 'politics',
    place: WASHINGTON,
    wiki: 'Withdrawal of Joe Biden from the 2024 United States presidential election',
    bursts: [['2024-07-21', 3000]],
    decayDays: 5,
  },
  {
    id: 'hist-paris-olympics',
    title: 'Paris Olympics open on the Seine',
    category: 'other',
    place: PARIS,
    wiki: '2024 Summer Olympics',
    bursts: [
      ['2024-07-27', 1500],
      ['2024-08-11', 800],
    ],
    decayDays: 7,
  },
  {
    id: 'hist-hasina',
    title: 'Sheikh Hasina resigns and flees Bangladesh',
    category: 'politics',
    place: p('Dhaka', 'BD', 23.81, 90.41),
    wiki: 'Resignation of Sheikh Hasina',
    bursts: [
      ['2024-07-20', 400],
      ['2024-08-05', 1500],
    ],
  },
  {
    id: 'hist-mpox',
    title: 'WHO declares mpox a global health emergency',
    category: 'health',
    place: p('Kinshasa', 'CD', -4.32, 15.31),
    also: [GENEVA],
    wiki: 'Mpox',
    bursts: [['2024-08-14', 1100]],
    decayDays: 9,
  },
  {
    id: 'hist-lebanon-pagers',
    title: 'Exploding pagers wound thousands in Lebanon',
    category: 'conflict',
    place: p('Beirut', 'LB', 33.89, 35.5),
    wiki: '2024 Lebanon electronic device attacks',
    bursts: [
      ['2024-09-17', 2200],
      ['2024-09-28', 1800],
    ],
    decayDays: 7,
  },
  {
    id: 'hist-helene',
    title: 'Hurricane Helene floods Asheville and the US Southeast',
    category: 'disaster',
    place: p('Asheville, North Carolina', 'US', 35.6, -82.55),
    also: [p('Perry, Florida', 'US', 30.12, -83.58)],
    wiki: 'Hurricane Helene',
    bursts: [['2024-09-27', 1400]],
    decayDays: 8,
  },
  {
    id: 'hist-valencia',
    title: 'Flash floods kill more than 200 around Valencia',
    category: 'disaster',
    place: p('Valencia', 'ES', 39.47, -0.38),
    wiki: '2024 Spanish floods',
    bursts: [['2024-10-30', 1600]],
  },
  {
    id: 'hist-us-election',
    title: 'Trump wins the US presidential election',
    category: 'politics',
    place: WASHINGTON,
    wiki: '2024 United States presidential election',
    bursts: [
      ['2024-11-06', 5000],
      ['2025-01-20', 3000],
    ],
    decayDays: 6,
  },
  {
    id: 'hist-korea-martial-law',
    title: "South Korea's president declares martial law",
    category: 'politics',
    place: p('Seoul', 'KR', 37.57, 126.98),
    wiki: '2024 South Korean martial law crisis',
    bursts: [
      ['2024-12-04', 2000],
      ['2024-12-14', 1000],
    ],
  },
  {
    id: 'hist-assad',
    title: 'Assad government falls as rebels take Damascus',
    category: 'conflict',
    place: p('Damascus', 'SY', 33.51, 36.29),
    also: [ALEPPO],
    wiki: 'Fall of the Assad regime',
    bursts: [
      ['2024-11-30', 700],
      ['2024-12-08', 3000],
    ],
    decayDays: 7,
  },
  // 2025
  {
    id: 'hist-la-fires',
    title: 'Wildfires tear through Los Angeles',
    category: 'disaster',
    place: p('Pacific Palisades', 'US', 34.05, -118.53),
    also: [p('Altadena', 'US', 34.19, -118.13)],
    wiki: 'January 2025 Southern California wildfires',
    bursts: [['2025-01-08', 2500]],
    decayDays: 8,
  },
  {
    id: 'hist-deepseek',
    title: 'DeepSeek AI model triggers a tech-stock sell-off',
    category: 'technology',
    place: p('Hangzhou', 'CN', 30.27, 120.15),
    wiki: 'DeepSeek',
    bursts: [['2025-01-27', 1500]],
    decayDays: 4,
  },
  {
    id: 'hist-myanmar-quake',
    title: 'Magnitude 7.7 earthquake strikes central Myanmar',
    category: 'disaster',
    place: p('Mandalay', 'MM', 21.97, 96.08),
    also: [p('Bangkok', 'TH', 13.76, 100.5)],
    wiki: '2025 Myanmar earthquake',
    bursts: [['2025-03-28', 2000]],
  },
  {
    id: 'hist-tariffs',
    title: "US announces sweeping 'Liberation Day' tariffs",
    category: 'economy',
    place: WASHINGTON,
    wiki: 'Liberation Day tariffs',
    bursts: [
      ['2025-04-03', 3000],
      ['2025-04-09', 2000],
    ],
    decayDays: 6,
  },
  {
    id: 'hist-pope-francis',
    title: 'Pope Francis dies at 88',
    category: 'other',
    place: p('Vatican City', 'VA', 41.9, 12.45),
    wiki: 'Death and funeral of Pope Francis',
    bursts: [
      ['2025-04-21', 3000],
      ['2025-04-26', 1500],
    ],
    decayDays: 5,
  },
  {
    id: 'hist-pahalgam',
    title: 'Gunmen kill tourists in Pahalgam, Kashmir',
    category: 'conflict',
    place: p('Pahalgam', 'IN', 34.02, 75.32),
    wiki: '2025 Pahalgam attack',
    bursts: [['2025-04-22', 1500]],
    decayDays: 7,
  },
  {
    id: 'hist-iberia-blackout',
    title: 'Blackout cuts power across Spain and Portugal',
    category: 'technology',
    place: p('Madrid', 'ES', 40.42, -3.7),
    also: [p('Lisbon', 'PT', 38.72, -9.14)],
    wiki: '2025 Iberian Peninsula blackout',
    bursts: [['2025-04-28', 1800]],
    decayDays: 3,
  },
  {
    id: 'hist-india-pakistan',
    title: 'India and Pakistan exchange military strikes',
    category: 'conflict',
    place: p('Islamabad', 'PK', 33.69, 73.06),
    also: [NEW_DELHI, p('Bahawalpur', 'PK', 29.4, 71.68)],
    wiki: '2025 India–Pakistan conflict',
    bursts: [
      ['2025-05-07', 2200],
      ['2025-05-10', 1500],
    ],
    decayDays: 5,
  },
  {
    id: 'hist-conclave',
    title: 'Conclave elects Leo XIV, the first American pope',
    category: 'other',
    place: p('Vatican City', 'VA', 41.9, 12.45),
    wiki: 'Pope Leo XIV',
    bursts: [['2025-05-08', 2500]],
    decayDays: 4,
  },
  {
    id: 'hist-air-india',
    title: 'Air India flight crashes after take-off in Ahmedabad',
    category: 'disaster',
    place: p('Ahmedabad', 'IN', 23.03, 72.58),
    wiki: 'Air India Flight 171',
    bursts: [['2025-06-12', 2000]],
  },
  {
    id: 'hist-israel-iran-war',
    title: 'Israel and Iran at war',
    category: 'conflict',
    place: TEHRAN,
    also: [TEL_AVIV, p('Fordow', 'IR', 34.88, 50.99)],
    wiki: 'Iran–Israel war',
    bursts: [
      ['2025-06-13', 3500],
      ['2025-06-22', 3000],
      ['2025-06-24', 1500],
    ],
    decayDays: 5,
  },
  {
    id: 'hist-texas-floods',
    title: 'Flash floods sweep Texas Hill Country',
    category: 'disaster',
    place: p('Kerrville, Texas', 'US', 30.05, -99.14),
    wiki: '2025 Central Texas floods',
    bursts: [['2025-07-05', 1800]],
  },
  {
    id: 'hist-kamchatka',
    title: 'Magnitude 8.8 earthquake off Kamchatka sets off Pacific tsunami alerts',
    category: 'disaster',
    place: p('Petropavlovsk-Kamchatsky', 'RU', 53.02, 158.65),
    wiki: '2025 Kamchatka earthquake',
    bursts: [['2025-07-30', 1500]],
    decayDays: 3,
  },
  {
    id: 'hist-nepal',
    title: "Youth-led protests topple Nepal's government",
    category: 'society',
    place: p('Kathmandu', 'NP', 27.72, 85.32),
    wiki: '2025 Nepalese Gen Z protests',
    bursts: [
      ['2025-09-08', 800],
      ['2025-09-09', 1300],
    ],
  },
  {
    id: 'hist-us-shutdown',
    title: 'US federal government shuts down',
    category: 'politics',
    place: WASHINGTON,
    wiki: '2025 United States federal government shutdown',
    bursts: [
      ['2025-10-01', 1500],
      ['2025-11-10', 900],
    ],
    ongoing: 150,
    end: '2025-11-13',
  },
  {
    id: 'hist-louvre',
    title: 'Jewels stolen in a daylight heist at the Louvre',
    category: 'other',
    place: PARIS,
    wiki: '2025 Louvre robbery',
    bursts: [['2025-10-19', 1500]],
    decayDays: 5,
  },
  {
    id: 'hist-melissa',
    title: 'Hurricane Melissa hits Jamaica as a Category 5 storm',
    category: 'disaster',
    place: p('Black River', 'JM', 18.03, -77.85),
    also: [p('Santiago de Cuba', 'CU', 20.02, -75.82)],
    wiki: 'Hurricane Melissa',
    bursts: [['2025-10-28', 1600]],
  },
  // 2026
  {
    id: 'hist-milan-olympics',
    title: 'Winter Olympics open in Milan and Cortina',
    category: 'other',
    place: p('Milan', 'IT', 45.46, 9.19),
    also: [p("Cortina d'Ampezzo", 'IT', 46.54, 12.14)],
    wiki: '2026 Winter Olympics',
    bursts: [
      ['2026-02-06', 1200],
      ['2026-02-22', 600],
    ],
    decayDays: 8,
  },
  {
    id: 'hist-world-cup-2026',
    title: 'World Cup kicks off across the US, Canada and Mexico',
    category: 'other',
    place: MEXICO_CITY,
    also: [
      p('East Rutherford, New Jersey', 'US', 40.81, -74.07),
      p('Toronto', 'CA', 43.65, -79.38),
    ],
    wiki: '2026 FIFA World Cup',
    bursts: [
      ['2026-06-11', 1500],
      ['2026-07-19', 1800],
    ],
    decayDays: 8,
  },
]

// Mid-sized events, so that something new turns up every week or two while the bar plays.
const NEW_YORK = p('New York', 'US', 40.71, -74.01)
const LOS_ANGELES = p('Los Angeles', 'US', 34.05, -118.25)
const BERLIN = p('Berlin', 'DE', 52.52, 13.4)
const SEOUL = p('Seoul', 'KR', 37.57, 126.98)
const THE_HAGUE = p('The Hague', 'NL', 52.08, 4.3)
const OTTAWA = p('Ottawa', 'CA', 45.42, -75.7)
const SHANGHAI = p('Shanghai', 'CN', 31.23, 121.47)
const BUFFALO = p('Buffalo, New York', 'US', 42.89, -78.88)
const JERUSALEM = p('Jerusalem', null, 31.77, 35.21)
const DOHA = p('Doha', 'QA', 25.29, 51.53)
const DUBAI = p('Dubai', 'AE', 25.2, 55.27)
const ISLAMABAD = p('Islamabad', 'PK', 33.69, 73.06)

const MORE_EVENTS: HistoryEvent[] = [
  // 2021
  {
    id: 'hist-la-palma',
    title: 'La Palma volcano keeps erupting',
    category: 'disaster',
    place: p('La Palma', 'ES', 28.61, -17.87),
    wiki: '2021 Cumbre Vieja volcanic eruption',
    bursts: [
      ['2021-09-19', 1200],
      ['2021-11-28', 300],
    ],
    ongoing: 60,
    end: '2021-12-25',
  },
  {
    id: 'hist-belarus-border',
    title: 'Migrants stranded at the Belarus–Poland border',
    category: 'society',
    place: p('Kuźnica', 'PL', 53.51, 23.65),
    wiki: '2021 Belarus–European Union border crisis',
    bursts: [['2021-11-08', 800]],
    decayDays: 10,
  },
  {
    id: 'hist-cop26',
    title: 'COP26 climate summit ends with the Glasgow pact',
    category: 'disaster',
    place: p('Glasgow', 'GB', 55.86, -4.25),
    wiki: '2021 United Nations Climate Change Conference',
    bursts: [['2021-11-13', 700]],
  },
  {
    id: 'hist-rittenhouse',
    title: 'Kyle Rittenhouse acquitted',
    category: 'society',
    place: p('Kenosha, Wisconsin', 'US', 42.58, -87.82),
    wiki: 'Kenosha unrest shooting',
    bursts: [['2021-11-19', 900]],
    decayDays: 4,
  },
  {
    id: 'hist-scholz',
    title: 'Olaf Scholz becomes German chancellor',
    category: 'politics',
    place: BERLIN,
    wiki: 'Scholz cabinet',
    bursts: [['2021-12-08', 500]],
  },
  {
    id: 'hist-typhoon-rai',
    title: 'Typhoon Rai devastates the central Philippines',
    category: 'disaster',
    place: p('Surigao', 'PH', 9.79, 125.49),
    also: [p('Cebu', 'PH', 10.32, 123.89)],
    wiki: 'Typhoon Rai',
    bursts: [['2021-12-17', 500]],
  },
  {
    id: 'hist-tutu',
    title: 'Desmond Tutu dies at 90',
    category: 'other',
    place: p('Cape Town', 'ZA', -33.92, 18.42),
    wiki: 'Desmond Tutu',
    bursts: [['2021-12-26', 900]],
    decayDays: 4,
  },
  {
    id: 'hist-marshall-fire',
    title: 'Marshall Fire burns homes near Boulder, Colorado',
    category: 'disaster',
    place: p('Louisville, Colorado', 'US', 39.98, -105.13),
    wiki: 'Marshall Fire',
    bursts: [['2021-12-31', 600]],
  },
  // 2022
  {
    id: 'hist-djokovic',
    title: 'Australia deports Novak Djokovic over vaccine rules',
    category: 'other',
    place: p('Melbourne', 'AU', -37.81, 144.96),
    wiki: 'Novak Djokovic Australian visa controversy',
    bursts: [
      ['2022-01-06', 500],
      ['2022-01-16', 900],
    ],
    decayDays: 4,
  },
  {
    id: 'hist-burkina-coup',
    title: 'Military takes power in Burkina Faso',
    category: 'politics',
    place: p('Ouagadougou', 'BF', 12.37, -1.52),
    wiki: "January 2022 Burkina Faso coup d'état",
    bursts: [['2022-01-24', 400]],
  },
  {
    id: 'hist-ottawa-convoy',
    title: 'Trucker convoy protest occupies Ottawa',
    category: 'society',
    place: OTTAWA,
    wiki: 'Canada convoy protest',
    bursts: [
      ['2022-01-29', 500],
      ['2022-02-14', 800],
    ],
    decayDays: 7,
  },
  {
    id: 'hist-china-eastern',
    title: 'China Eastern flight crashes in Guangxi',
    category: 'disaster',
    place: p('Wuzhou', 'CN', 23.48, 111.28),
    wiki: 'China Eastern Airlines Flight 5735',
    bursts: [['2022-03-21', 900]],
    decayDays: 4,
  },
  {
    id: 'hist-oscars-slap',
    title: 'Will Smith slaps Chris Rock at the Oscars',
    category: 'other',
    place: LOS_ANGELES,
    wiki: 'Will Smith–Chris Rock slapping incident',
    bursts: [['2022-03-28', 1500]],
    decayDays: 3,
  },
  {
    id: 'hist-shanghai-lockdown',
    title: 'Shanghai locks down to fight Covid',
    category: 'health',
    place: SHANGHAI,
    wiki: '2022 Shanghai COVID-19 outbreak',
    bursts: [
      ['2022-03-28', 800],
      ['2022-04-12', 600],
    ],
    ongoing: 120,
    end: '2022-06-05',
  },
  {
    id: 'hist-imran-khan',
    title: "Imran Khan ousted as Pakistan's prime minister",
    category: 'politics',
    place: ISLAMABAD,
    wiki: 'No-confidence motion against Imran Khan',
    bursts: [['2022-04-10', 700]],
  },
  {
    id: 'hist-twitter-musk',
    title: 'Elon Musk buys Twitter',
    category: 'technology',
    place: SAN_FRANCISCO,
    wiki: 'Acquisition of Twitter by Elon Musk',
    bursts: [
      ['2022-04-26', 900],
      ['2022-10-28', 1300],
    ],
  },
  {
    id: 'hist-roe',
    title: 'US Supreme Court moves to overturn Roe v. Wade',
    category: 'politics',
    place: WASHINGTON,
    wiki: "Dobbs v. Jackson Women's Health Organization",
    bursts: [
      ['2022-05-03', 1200],
      ['2022-06-24', 2500],
    ],
  },
  {
    id: 'hist-terra-luna',
    title: 'TerraUSD stablecoin collapses, dragging down crypto',
    category: 'economy',
    place: SEOUL,
    wiki: 'Terra (blockchain)',
    bursts: [['2022-05-11', 700]],
  },
  {
    id: 'hist-buffalo-shooting',
    title: 'Gunman kills 10 at a Buffalo supermarket',
    category: 'society',
    place: BUFFALO,
    wiki: '2022 Buffalo shooting',
    bursts: [['2022-05-15', 1100]],
  },
  {
    id: 'hist-monkeypox',
    title: 'Monkeypox cases spread outside Africa',
    category: 'health',
    place: LONDON,
    also: [p('Madrid', 'ES', 40.42, -3.7), p('Lisbon', 'PT', 38.72, -9.14)],
    wiki: '2022–2023 mpox outbreak',
    bursts: [
      ['2022-05-20', 600],
      ['2022-07-24', 900],
    ],
    decayDays: 10,
  },
  {
    id: 'hist-afghan-quake-2022',
    title: 'Earthquake kills more than 1,000 in eastern Afghanistan',
    category: 'disaster',
    place: p('Paktika', 'AF', 33.0, 69.5),
    wiki: 'June 2022 Afghanistan earthquake',
    bursts: [['2022-06-22', 700]],
  },
  {
    id: 'hist-boris-resigns',
    title: 'Boris Johnson resigns as Conservative leader',
    category: 'politics',
    place: LONDON,
    wiki: 'July 2022 United Kingdom government crisis',
    bursts: [['2022-07-07', 1500]],
    decayDays: 5,
  },
  {
    id: 'hist-uk-heat',
    title: 'UK records 40°C for the first time',
    category: 'disaster',
    place: LONDON,
    wiki: '2022 United Kingdom heat waves',
    bursts: [['2022-07-19', 900]],
    decayDays: 4,
  },
  {
    id: 'hist-mar-a-lago',
    title: "FBI searches Trump's Mar-a-Lago estate",
    category: 'politics',
    place: p('Palm Beach, Florida', 'US', 26.68, -80.04),
    wiki: 'FBI search of Mar-a-Lago',
    bursts: [['2022-08-09', 1300]],
  },
  {
    id: 'hist-rushdie',
    title: 'Salman Rushdie stabbed on stage in New York state',
    category: 'other',
    place: p('Chautauqua, New York', 'US', 42.21, -79.47),
    wiki: 'Stabbing of Salman Rushdie',
    bursts: [['2022-08-12', 1000]],
    decayDays: 4,
  },
  {
    id: 'hist-gorbachev',
    title: 'Mikhail Gorbachev dies at 91',
    category: 'other',
    place: MOSCOW,
    wiki: 'Mikhail Gorbachev',
    bursts: [['2022-08-31', 900]],
    decayDays: 3,
  },
  {
    id: 'hist-mobilisation',
    title: 'Putin orders a partial mobilisation',
    category: 'conflict',
    place: MOSCOW,
    wiki: '2022 Russian mobilization',
    bursts: [['2022-09-21', 1300]],
  },
  {
    id: 'hist-meloni',
    title: "Giorgia Meloni's party wins Italy's election",
    category: 'politics',
    place: p('Rome', 'IT', 41.9, 12.5),
    wiki: '2022 Italian general election',
    bursts: [['2022-09-26', 700]],
  },
  {
    id: 'hist-ian',
    title: 'Hurricane Ian hits southwest Florida',
    category: 'disaster',
    place: p('Fort Myers, Florida', 'US', 26.64, -81.87),
    wiki: 'Hurricane Ian',
    bursts: [['2022-09-28', 1600]],
  },
  {
    id: 'hist-crimea-bridge',
    title: 'Explosion damages the Crimean Bridge',
    category: 'conflict',
    place: p('Kerch Strait', null, 45.31, 36.51),
    wiki: '2022 Crimean Bridge explosion',
    bursts: [['2022-10-08', 1100]],
  },
  {
    id: 'hist-xi-third-term',
    title: 'Xi Jinping secures a third term as party leader',
    category: 'politics',
    place: p('Beijing', 'CN', 39.9, 116.41),
    wiki: '20th National Congress of the Chinese Communist Party',
    bursts: [
      ['2022-10-16', 500],
      ['2022-10-23', 900],
    ],
  },
  {
    id: 'hist-itaewon',
    title: "Crowd crush kills more than 150 in Seoul's Itaewon",
    category: 'disaster',
    place: SEOUL,
    wiki: 'Seoul Halloween crowd crush',
    bursts: [['2022-10-30', 1300]],
  },
  {
    id: 'hist-us-midterms',
    title: 'US midterm elections',
    category: 'politics',
    place: WASHINGTON,
    wiki: '2022 United States elections',
    bursts: [['2022-11-09', 1600]],
    decayDays: 5,
  },
  {
    id: 'hist-cop27',
    title: 'COP27 agrees a loss-and-damage fund',
    category: 'disaster',
    place: p('Sharm el-Sheikh', 'EG', 27.92, 34.33),
    wiki: '2022 United Nations Climate Change Conference',
    bursts: [
      ['2022-11-07', 400],
      ['2022-11-20', 600],
    ],
  },
  {
    id: 'hist-china-covid-protests',
    title: 'Protests against zero-Covid rules spread across China',
    category: 'society',
    place: SHANGHAI,
    also: [p('Ürümqi', 'CN', 43.83, 87.62), p('Beijing', 'CN', 39.9, 116.41)],
    wiki: '2022 COVID-19 protests in China',
    bursts: [
      ['2022-11-27', 1100],
      ['2022-12-07', 800],
    ],
  },
  {
    id: 'hist-peru-castillo',
    title: "Peru's President Castillo ousted and arrested",
    category: 'politics',
    place: p('Lima', 'PE', -12.05, -77.04),
    wiki: '2022 Peruvian self-coup attempt',
    bursts: [['2022-12-08', 700]],
  },
  {
    id: 'hist-fusion',
    title: 'US lab achieves fusion ignition',
    category: 'technology',
    place: p('Livermore, California', 'US', 37.68, -121.77),
    wiki: 'National Ignition Facility',
    bursts: [['2022-12-13', 800]],
    decayDays: 3,
  },
  {
    id: 'hist-storm-elliott',
    title: 'Winter storm buries Buffalo over Christmas',
    category: 'disaster',
    place: BUFFALO,
    wiki: 'December 2022 North American winter storm',
    bursts: [['2022-12-24', 900]],
  },
  {
    id: 'hist-pele',
    title: 'Pelé dies at 82',
    category: 'other',
    place: p('São Paulo', 'BR', -23.55, -46.63),
    also: [p('Santos', 'BR', -23.96, -46.33)],
    wiki: 'Pelé',
    bursts: [['2022-12-30', 1300]],
    decayDays: 4,
  },
  // 2023
  {
    id: 'hist-mccarthy-speaker',
    title: 'Kevin McCarthy elected speaker after 15 votes',
    category: 'politics',
    place: WASHINGTON,
    wiki: 'January 2023 Speaker of the United States House of Representatives election',
    bursts: [['2023-01-07', 700]],
    decayDays: 3,
  },
  {
    id: 'hist-ardern',
    title: "Jacinda Ardern resigns as New Zealand's prime minister",
    category: 'politics',
    place: p('Wellington', 'NZ', -41.29, 174.78),
    wiki: 'Jacinda Ardern',
    bursts: [['2023-01-19', 800]],
    decayDays: 4,
  },
  {
    id: 'hist-france-pensions',
    title: "Strikes and protests over France's pension reform",
    category: 'society',
    place: PARIS,
    wiki: '2023 French pension reform strikes',
    bursts: [
      ['2023-01-19', 600],
      ['2023-03-16', 900],
      ['2023-03-23', 800],
    ],
    ongoing: 80,
    end: '2023-04-20',
  },
  {
    id: 'hist-tyre-nichols',
    title: "Video of Tyre Nichols' beating by police released",
    category: 'society',
    place: p('Memphis, Tennessee', 'US', 35.15, -90.05),
    wiki: 'Killing of Tyre Nichols',
    bursts: [['2023-01-28', 1000]],
  },
  {
    id: 'hist-spy-balloon',
    title: 'US shoots down a Chinese balloon off South Carolina',
    category: 'politics',
    place: p('Myrtle Beach, South Carolina', 'US', 33.69, -78.89),
    wiki: '2023 Chinese balloon incident',
    bursts: [
      ['2023-02-03', 900],
      ['2023-02-05', 1100],
    ],
    decayDays: 4,
  },
  {
    id: 'hist-tempi',
    title: 'Train collision near Tempi kills 57 in Greece',
    category: 'disaster',
    place: p('Tempi', 'GR', 39.88, 22.59),
    wiki: 'Tempi train crash',
    bursts: [['2023-03-01', 800]],
  },
  {
    id: 'hist-putin-icc',
    title: 'ICC issues an arrest warrant for Vladimir Putin',
    category: 'politics',
    place: THE_HAGUE,
    wiki: 'ICC arrest warrant for Vladimir Putin',
    bursts: [['2023-03-17', 1000]],
    decayDays: 4,
  },
  {
    id: 'hist-israel-judicial',
    title: 'Mass protests over Israel’s judicial overhaul',
    category: 'politics',
    place: JERUSALEM,
    also: [TEL_AVIV],
    wiki: '2023 Israeli judicial reform',
    bursts: [
      ['2023-03-27', 900],
      ['2023-07-25', 900],
    ],
  },
  {
    id: 'hist-trump-indicted',
    title: 'Trump indicted in New York',
    category: 'politics',
    place: NEW_YORK,
    wiki: 'Prosecution of Donald Trump in New York',
    bursts: [
      ['2023-03-31', 1500],
      ['2023-04-04', 1300],
    ],
  },
  {
    id: 'hist-starship',
    title: 'SpaceX Starship explodes on its first test flight',
    category: 'technology',
    place: p('Boca Chica, Texas', 'US', 25.99, -97.19),
    wiki: 'Starship flight test 1',
    bursts: [['2023-04-20', 800]],
    decayDays: 3,
  },
  {
    id: 'hist-emilia-floods',
    title: "Floods swamp Italy's Emilia-Romagna",
    category: 'disaster',
    place: p('Faenza', 'IT', 44.29, 11.88),
    wiki: '2023 Emilia-Romagna floods',
    bursts: [['2023-05-17', 600]],
  },
  {
    id: 'hist-debt-ceiling',
    title: 'US debt-ceiling standoff ends with a last-minute deal',
    category: 'economy',
    place: WASHINGTON,
    wiki: '2023 United States debt-ceiling crisis',
    bursts: [
      ['2023-05-16', 600],
      ['2023-05-28', 900],
    ],
    decayDays: 5,
  },
  {
    id: 'hist-odisha',
    title: 'Train crash in Odisha kills nearly 300',
    category: 'disaster',
    place: p('Balasore', 'IN', 21.49, 86.93),
    wiki: '2023 Odisha train collision',
    bursts: [['2023-06-03', 1300]],
  },
  {
    id: 'hist-canada-smoke',
    title: "Canadian wildfire smoke turns New York's sky orange",
    category: 'disaster',
    place: NEW_YORK,
    also: [p('Quebec', 'CA', 49.0, -74.0)],
    wiki: '2023 Canadian wildfires',
    bursts: [['2023-06-07', 1300]],
    decayDays: 4,
  },
  {
    id: 'hist-pylos',
    title: 'Migrant boat sinks off Pylos, Greece',
    category: 'society',
    place: p('Pylos', 'GR', 36.91, 21.7),
    wiki: '2023 Messenia migrant boat disaster',
    bursts: [['2023-06-14', 700]],
  },
  {
    id: 'hist-nahel',
    title: 'Riots across France after police kill a teenager in Nanterre',
    category: 'society',
    place: p('Nanterre', 'FR', 48.89, 2.21),
    wiki: 'Killing of Nahel Merzouk',
    bursts: [
      ['2023-06-28', 800],
      ['2023-07-01', 1000],
    ],
  },
  {
    id: 'hist-brics-expansion',
    title: 'BRICS invites six new members',
    category: 'economy',
    place: p('Johannesburg', 'ZA', -26.2, 28.05),
    wiki: '15th BRICS summit',
    bursts: [['2023-08-24', 600]],
  },
  {
    id: 'hist-fukushima-water',
    title: 'Japan starts releasing treated Fukushima water',
    category: 'disaster',
    place: p('Fukushima', 'JP', 37.42, 141.03),
    wiki: 'Discharge of radioactive water of the Fukushima Daiichi Nuclear Power Plant',
    bursts: [['2023-08-24', 600]],
  },
  {
    id: 'hist-trump-mugshot',
    title: 'Trump surrenders in Georgia; his mugshot is released',
    category: 'politics',
    place: p('Atlanta', 'US', 33.75, -84.39),
    wiki: 'Prosecution of Donald Trump in Georgia',
    bursts: [['2023-08-25', 1200]],
    decayDays: 3,
  },
  {
    id: 'hist-gabon-coup',
    title: 'Military seizes power in Gabon',
    category: 'politics',
    place: p('Libreville', 'GA', 0.42, 9.47),
    wiki: "2023 Gabonese coup d'état",
    bursts: [['2023-08-30', 600]],
  },
  {
    id: 'hist-g20-delhi',
    title: 'G20 summit in New Delhi admits the African Union',
    category: 'politics',
    place: NEW_DELHI,
    wiki: '2023 G20 New Delhi summit',
    bursts: [['2023-09-09', 600]],
    decayDays: 4,
  },
  {
    id: 'hist-karabakh',
    title: 'Azerbaijan takes Nagorno-Karabakh; Armenians flee',
    category: 'conflict',
    place: p('Stepanakert', 'AZ', 39.82, 46.75),
    wiki: '2023 Azerbaijani offensive in Nagorno-Karabakh',
    bursts: [
      ['2023-09-19', 800],
      ['2023-09-26', 700],
    ],
  },
  {
    id: 'hist-mccarthy-ousted',
    title: 'House ousts Speaker Kevin McCarthy',
    category: 'politics',
    place: WASHINGTON,
    wiki: 'Removal of Kevin McCarthy as Speaker of the House',
    bursts: [['2023-10-04', 1000]],
    decayDays: 4,
  },
  {
    id: 'hist-otis',
    title: 'Hurricane Otis wrecks Acapulco',
    category: 'disaster',
    place: p('Acapulco', 'MX', 16.85, -99.88),
    wiki: 'Hurricane Otis',
    bursts: [['2023-10-25', 700]],
  },
  {
    id: 'hist-lewiston',
    title: 'Gunman kills 18 in Lewiston, Maine',
    category: 'society',
    place: p('Lewiston, Maine', 'US', 44.1, -70.21),
    wiki: '2023 Lewiston shootings',
    bursts: [['2023-10-26', 900]],
    decayDays: 4,
  },
  {
    id: 'hist-grindavik',
    title: 'Iceland evacuates Grindavík as a volcano stirs',
    category: 'disaster',
    place: p('Grindavík', 'IS', 63.84, -22.43),
    wiki: 'Sundhnúkur eruptions',
    bursts: [
      ['2023-11-11', 500],
      ['2023-12-19', 800],
    ],
  },
  {
    id: 'hist-red-sea',
    title: 'Houthi attacks disrupt Red Sea shipping',
    category: 'conflict',
    place: p('Bab el-Mandeb', null, 12.6, 43.3),
    also: [p("Sana'a", 'YE', 15.37, 44.19)],
    wiki: 'Red Sea crisis',
    bursts: [
      ['2023-11-20', 500],
      ['2023-12-18', 900],
      ['2024-01-12', 1300],
    ],
    decayDays: 7,
    ongoing: 60,
    end: '2024-06-30',
  },
  {
    id: 'hist-wilders',
    title: "Geert Wilders' party wins the Dutch election",
    category: 'politics',
    place: THE_HAGUE,
    wiki: '2023 Dutch general election',
    bursts: [['2023-11-23', 600]],
  },
  {
    id: 'hist-kissinger',
    title: 'Henry Kissinger dies at 100',
    category: 'other',
    place: p('Kent, Connecticut', 'US', 41.72, -73.48),
    wiki: 'Henry Kissinger',
    bursts: [['2023-11-30', 900]],
    decayDays: 3,
  },
  {
    id: 'hist-cop28',
    title: 'COP28 deal calls for a transition away from fossil fuels',
    category: 'disaster',
    place: DUBAI,
    wiki: '2023 United Nations Climate Change Conference',
    bursts: [
      ['2023-11-30', 500],
      ['2023-12-13', 900],
    ],
  },
  // 2024
  {
    id: 'hist-alaska-door',
    title: 'Door plug blows out on an Alaska Airlines Boeing 737 Max',
    category: 'other',
    place: p('Portland, Oregon', 'US', 45.52, -122.68),
    wiki: 'Alaska Airlines Flight 1282',
    bursts: [['2024-01-06', 1200]],
  },
  {
    id: 'hist-ecuador',
    title: 'Gunmen storm an Ecuadorian TV studio live on air',
    category: 'conflict',
    place: p('Guayaquil', 'EC', -2.17, -79.92),
    wiki: '2024 Ecuadorian conflict',
    bursts: [['2024-01-10', 800]],
  },
  {
    id: 'hist-icj-genocide',
    title: 'ICJ orders Israel to prevent genocidal acts in Gaza',
    category: 'politics',
    place: THE_HAGUE,
    wiki: 'South Africa v. Israel (Genocide Convention)',
    bursts: [
      ['2024-01-11', 500],
      ['2024-01-26', 1100],
    ],
  },
  {
    id: 'hist-eu-farmers',
    title: "Farmers' protests block roads across Europe",
    category: 'society',
    place: p('Brussels', 'BE', 50.85, 4.35),
    also: [PARIS, BERLIN],
    wiki: '2024 European farmers protests',
    bursts: [
      ['2024-01-26', 500],
      ['2024-02-01', 800],
    ],
    decayDays: 8,
  },
  {
    id: 'hist-putin-reelected',
    title: 'Putin wins a fifth term',
    category: 'politics',
    place: MOSCOW,
    wiki: '2024 Russian presidential election',
    bursts: [['2024-03-18', 700]],
    decayDays: 3,
  },
  {
    id: 'hist-kate-cancer',
    title: 'Princess of Wales reveals a cancer diagnosis',
    category: 'other',
    place: LONDON,
    wiki: 'Catherine, Princess of Wales',
    bursts: [['2024-03-23', 1300]],
    decayDays: 3,
  },
  {
    id: 'hist-hualien',
    title: 'Strongest earthquake in 25 years hits Taiwan',
    category: 'disaster',
    place: p('Hualien', 'TW', 23.99, 121.6),
    wiki: '2024 Hualien earthquake',
    bursts: [['2024-04-03', 1100]],
    decayDays: 4,
  },
  {
    id: 'hist-dubai-floods',
    title: 'Record rain floods Dubai',
    category: 'disaster',
    place: DUBAI,
    wiki: '2024 United Arab Emirates floods',
    bursts: [['2024-04-17', 800]],
    decayDays: 3,
  },
  {
    id: 'hist-campus-protests',
    title: 'Gaza protests spread across US campuses',
    category: 'society',
    place: NEW_YORK,
    also: [LOS_ANGELES],
    wiki: '2024 pro-Palestinian protests on university campuses',
    bursts: [
      ['2024-04-22', 800],
      ['2024-05-01', 1100],
    ],
    decayDays: 7,
  },
  {
    id: 'hist-rio-grande-floods',
    title: "Floods devastate Brazil's Rio Grande do Sul",
    category: 'disaster',
    place: p('Porto Alegre', 'BR', -30.03, -51.23),
    wiki: '2024 Rio Grande do Sul floods',
    bursts: [['2024-05-04', 700]],
    decayDays: 10,
  },
  {
    id: 'hist-fico',
    title: 'Slovak Prime Minister Robert Fico shot',
    category: 'politics',
    place: p('Handlová', 'SK', 48.73, 18.76),
    wiki: 'Attempted assassination of Robert Fico',
    bursts: [['2024-05-15', 1000]],
    decayDays: 4,
  },
  {
    id: 'hist-png-landslide',
    title: 'Landslide buries villages in Papua New Guinea',
    category: 'disaster',
    place: p('Enga', 'PG', -5.36, 143.4),
    wiki: '2024 Enga landslide',
    bursts: [['2024-05-25', 500]],
  },
  {
    id: 'hist-trump-convicted',
    title: 'Trump found guilty in his New York hush-money trial',
    category: 'politics',
    place: NEW_YORK,
    wiki: 'Prosecution of Donald Trump in New York',
    bursts: [['2024-05-31', 2000]],
    decayDays: 4,
  },
  {
    id: 'hist-france-snap-election',
    title: 'Far right surges in EU vote; Macron calls a snap election',
    category: 'politics',
    place: PARIS,
    wiki: '2024 French legislative election',
    bursts: [
      ['2024-06-10', 1300],
      ['2024-07-08', 1200],
    ],
  },
  {
    id: 'hist-kenya-protests',
    title: "Protesters storm Kenya's parliament over tax rises",
    category: 'society',
    place: p('Nairobi', 'KE', -1.29, 36.82),
    wiki: '2024 Kenya Finance Bill protests',
    bursts: [['2024-06-26', 900]],
  },
  {
    id: 'hist-debate',
    title: 'Biden stumbles in his debate with Trump',
    category: 'politics',
    place: p('Atlanta', 'US', 33.75, -84.39),
    wiki: '2024 Joe Biden–Donald Trump presidential debate',
    bursts: [['2024-06-28', 2000]],
    decayDays: 4,
  },
  {
    id: 'hist-venezuela-election',
    title: 'Maduro claims victory in a disputed Venezuelan election',
    category: 'politics',
    place: p('Caracas', 'VE', 10.48, -66.9),
    wiki: '2024 Venezuelan presidential election',
    bursts: [['2024-07-29', 900]],
  },
  {
    id: 'hist-southport',
    title: 'Riots spread across England after the Southport stabbings',
    category: 'society',
    place: p('Southport', 'GB', 53.65, -3.01),
    wiki: '2024 United Kingdom riots',
    bursts: [
      ['2024-07-30', 800],
      ['2024-08-04', 1100],
    ],
  },
  {
    id: 'hist-haniyeh',
    title: 'Hamas leader Ismail Haniyeh killed in Tehran',
    category: 'conflict',
    place: TEHRAN,
    wiki: 'Assassination of Ismail Haniyeh',
    bursts: [['2024-07-31', 1400]],
    decayDays: 4,
  },
  {
    id: 'hist-durov',
    title: 'Telegram founder Pavel Durov arrested in France',
    category: 'technology',
    place: PARIS,
    wiki: 'Pavel Durov',
    bursts: [['2024-08-25', 900]],
    decayDays: 4,
  },
  {
    id: 'hist-milton',
    title: "Hurricane Milton hits Florida's Gulf Coast",
    category: 'disaster',
    place: p('Siesta Key, Florida', 'US', 27.27, -82.55),
    wiki: 'Hurricane Milton',
    bursts: [['2024-10-09', 1500]],
    decayDays: 5,
  },
  {
    id: 'hist-cop29',
    title: 'COP29 in Baku agrees a climate finance target',
    category: 'disaster',
    place: p('Baku', 'AZ', 40.41, 49.87),
    wiki: '2024 United Nations Climate Change Conference',
    bursts: [
      ['2024-11-11', 400],
      ['2024-11-24', 600],
    ],
  },
  {
    id: 'hist-georgia-protests',
    title: 'Protests in Georgia over a halt to EU membership talks',
    category: 'society',
    place: p('Tbilisi', 'GE', 41.72, 44.79),
    wiki: '2024–2025 Georgian protests',
    bursts: [['2024-11-29', 700]],
    decayDays: 8,
  },
  {
    id: 'hist-ceo-shooting',
    title: 'UnitedHealthcare chief executive shot dead in Manhattan',
    category: 'society',
    place: NEW_YORK,
    wiki: 'Killing of Brian Thompson',
    bursts: [
      ['2024-12-05', 1300],
      ['2024-12-10', 1200],
    ],
    decayDays: 4,
  },
  {
    id: 'hist-notre-dame',
    title: 'Notre-Dame reopens five years after the fire',
    category: 'other',
    place: PARIS,
    wiki: 'Notre-Dame de Paris',
    bursts: [['2024-12-07', 900]],
    decayDays: 3,
  },
  {
    id: 'hist-chido',
    title: 'Cyclone Chido devastates Mayotte',
    category: 'disaster',
    place: p('Mamoudzou', 'YT', -12.78, 45.23),
    wiki: 'Cyclone Chido',
    bursts: [['2024-12-15', 700]],
  },
  {
    id: 'hist-jeju-air',
    title: 'Jeju Air crash at Muan kills 179',
    category: 'disaster',
    place: p('Muan', 'KR', 34.99, 126.38),
    wiki: 'Jeju Air Flight 2216',
    bursts: [['2024-12-29', 1300]],
    decayDays: 4,
  },
  // 2025
  {
    id: 'hist-new-orleans',
    title: "Truck attack kills 14 on New Orleans' Bourbon Street",
    category: 'society',
    place: p('New Orleans', 'US', 29.96, -90.07),
    wiki: '2025 New Orleans truck attack',
    bursts: [['2025-01-01', 1200]],
    decayDays: 4,
  },
  {
    id: 'hist-tiktok',
    title: 'TikTok briefly goes dark in the US',
    category: 'technology',
    place: WASHINGTON,
    wiki: 'Protecting Americans from Foreign Adversary Controlled Applications Act',
    bursts: [['2025-01-19', 1000]],
    decayDays: 3,
  },
  {
    id: 'hist-kumbh',
    title: 'Crush at the Kumbh Mela in Prayagraj',
    category: 'disaster',
    place: p('Prayagraj', 'IN', 25.44, 81.85),
    wiki: '2025 Prayagraj crowd crush',
    bursts: [['2025-01-29', 700]],
    decayDays: 3,
  },
  {
    id: 'hist-potomac-collision',
    title: 'Airliner and Army helicopter collide over Washington',
    category: 'disaster',
    place: WASHINGTON,
    wiki: '2025 Potomac River mid-air collision',
    bursts: [['2025-01-30', 1600]],
    decayDays: 4,
  },
  {
    id: 'hist-german-election',
    title: "Merz's CDU wins Germany's election; AfD comes second",
    category: 'politics',
    place: BERLIN,
    wiki: '2025 German federal election',
    bursts: [['2025-02-24', 1000]],
  },
  {
    id: 'hist-duterte',
    title: 'Rodrigo Duterte arrested and flown to The Hague',
    category: 'politics',
    place: p('Manila', 'PH', 14.6, 120.98),
    also: [THE_HAGUE],
    wiki: 'Arrest of Rodrigo Duterte',
    bursts: [
      ['2025-03-11', 900],
      ['2025-03-12', 800],
    ],
    decayDays: 4,
  },
  {
    id: 'hist-imamoglu',
    title: "Istanbul's mayor detained; mass protests across Turkey",
    category: 'society',
    place: p('Istanbul', 'TR', 41.01, 28.98),
    wiki: '2025 Turkish protests',
    bursts: [
      ['2025-03-19', 800],
      ['2025-03-23', 900],
    ],
  },
  {
    id: 'hist-canada-election',
    title: "Mark Carney's Liberals win Canada's election",
    category: 'politics',
    place: OTTAWA,
    wiki: '2025 Canadian federal election',
    bursts: [['2025-04-29', 800]],
    decayDays: 4,
  },
  {
    id: 'hist-korea-election',
    title: "Lee Jae-myung wins South Korea's snap election",
    category: 'politics',
    place: SEOUL,
    wiki: '2025 South Korean presidential election',
    bursts: [['2025-06-04', 700]],
  },
  {
    id: 'hist-la-protests',
    title: 'National Guard deployed amid Los Angeles immigration protests',
    category: 'society',
    place: LOS_ANGELES,
    wiki: 'June 2025 Los Angeles protests',
    bursts: [['2025-06-08', 1300]],
  },
  {
    id: 'hist-big-bill',
    title: 'Trump signs his tax and spending bill',
    category: 'economy',
    place: WASHINGTON,
    wiki: 'One Big Beautiful Bill Act',
    bursts: [
      ['2025-07-03', 600],
      ['2025-07-04', 900],
    ],
    decayDays: 4,
  },
  {
    id: 'hist-thai-cambodia',
    title: 'Thailand and Cambodia clash along their border',
    category: 'conflict',
    place: p('Surin', 'TH', 14.88, 103.49),
    wiki: '2025 Cambodia–Thailand border conflict',
    bursts: [['2025-07-24', 900]],
  },
  {
    id: 'hist-afghan-quake-2025',
    title: 'Earthquake kills hundreds in eastern Afghanistan',
    category: 'disaster',
    place: p('Kunar', 'AF', 34.85, 71.1),
    wiki: '2025 Afghanistan earthquake',
    bursts: [['2025-09-01', 800]],
  },
  {
    id: 'hist-doha-strike',
    title: 'Israel strikes Hamas leaders in Doha',
    category: 'conflict',
    place: DOHA,
    wiki: '2025 Israeli airstrike in Doha',
    bursts: [['2025-09-09', 1100]],
    decayDays: 4,
  },
  {
    id: 'hist-kirk',
    title: 'Charlie Kirk shot dead at a Utah university event',
    category: 'politics',
    place: p('Orem, Utah', 'US', 40.3, -111.69),
    wiki: 'Assassination of Charlie Kirk',
    bursts: [['2025-09-10', 2500]],
    decayDays: 5,
  },
  {
    id: 'hist-poland-drones',
    title: 'Russian drones shot down over Poland',
    category: 'conflict',
    place: p('Lublin', 'PL', 51.25, 22.57),
    wiki: '2025 Russian drone incursion into Poland',
    bursts: [['2025-09-10', 900]],
  },
  {
    id: 'hist-machado-nobel',
    title: 'María Corina Machado wins the Nobel Peace Prize',
    category: 'politics',
    place: p('Oslo', 'NO', 59.91, 10.75),
    also: [p('Caracas', 'VE', 10.48, -66.9)],
    wiki: 'María Corina Machado',
    bursts: [['2025-10-10', 700]],
    decayDays: 3,
  },
  {
    id: 'hist-mamdani',
    title: 'Zohran Mamdani elected mayor of New York',
    category: 'politics',
    place: NEW_YORK,
    wiki: '2025 New York City mayoral election',
    bursts: [['2025-11-05', 1300]],
    decayDays: 5,
  },
  {
    id: 'hist-cop30',
    title: 'COP30 climate summit opens in Belém',
    category: 'disaster',
    place: p('Belém', 'BR', -1.46, -48.49),
    wiki: '2025 United Nations Climate Change Conference',
    bursts: [['2025-11-10', 500]],
  },
]

const DEFAULT_DECAY_DAYS = 6
const DEFAULT_RADIUS_KM = 150
/** No story is on the globe with fewer articles than this in its last 7 days. */
const MIN_ARTICLES_7D = 25
/**
 * A story leaves the globe once its week falls below this share of its biggest week, so faded
 * stories don't linger for months at a trickle. Long-running stories (`ongoing`) stay while
 * their steady coverage lasts.
 */
const FADE_SHARE = 0.05
const SUMMARY =
  'Archive placeholder: a real event, but the coverage numbers are made up until the GDELT backfill reaches this date.'
const TONE: Record<Category, number> = {
  conflict: -6,
  disaster: -5,
  society: -3,
  health: -3,
  politics: -2,
  economy: -2,
  technology: 0,
  other: 0,
}

/** Events with their days pre-parsed, and the weekly coverage below which each one has faded. */
const PARSED = [...EVENTS, ...MORE_EVENTS].map((e) => {
  const decay = e.decayDays ?? DEFAULT_DECAY_DAYS
  // A burst's first week: half the peak on day 0, then the peak fading over days 1-6.
  let firstWeek = 0.5
  for (let k = 0; k < 6; k++) firstWeek += Math.exp(-k / decay)
  const peakWeek = Math.max(...e.bursts.map(([, peak]) => peak)) * firstWeek
  const fadedBelow = e.ongoing
    ? Math.max(MIN_ARTICLES_7D, 0.5 * e.ongoing * 7)
    : Math.max(MIN_ARTICLES_7D, FADE_SHARE * peakWeek)
  return {
    event: e,
    start: toIndex(e.bursts[0][0]),
    end: e.end ? toIndex(e.end) : Infinity,
    bursts: e.bursts.map(([day, peak]) => [toIndex(day), peak] as const),
    decay,
    fadedBelow,
  }
})
const BY_ID = new Map(PARSED.map((p) => [p.event.id, p]))

/** Deterministic noise in [0.8, 1.2], so the same day always gives the same numbers. */
function noise(seed: string, day: DayIndex): number {
  let h = 2166136261 ^ day
  for (let i = 0; i < seed.length; i++) h = Math.imul(h ^ seed.charCodeAt(i), 16777619)
  return 0.8 + 0.4 * (((h >>> 0) % 1000) / 1000)
}

/** Made-up articles per day for one event. */
function volume(parsed: (typeof PARSED)[number], day: DayIndex): number {
  if (day < parsed.start || day > parsed.end) return 0
  let v = 0
  for (const [burstDay, peak] of parsed.bursts) {
    const x = day - burstDay
    if (x < 0) continue
    // Half the peak on the day itself, the full peak the day after, then an exponential fade.
    v += peak * (x === 0 ? 0.5 : Math.exp(-(x - 1) / parsed.decay))
  }
  const ongoing = parsed.event.ongoing ?? 0
  if (ongoing) v += ongoing * Math.min(1, (day - parsed.start) / 7)
  return Math.round(v * noise(parsed.event.id, day))
}

function sum(parsed: (typeof PARSED)[number], from: DayIndex, to: DayIndex): number {
  let s = 0
  for (let d = from; d <= to; d++) s += volume(parsed, d)
  return s
}

const location = (place: Place): Location => ({ ...place })

const smoothstep = (lo: number, hi: number, x: number) => {
  const t = Math.min(1, Math.max(0, (x - lo) / (hi - lo)))
  return t * t * (3 - 2 * t)
}

/** The story on `day`, and how present it is (0..1: fading in or out around its threshold). */
function summary(
  parsed: (typeof PARSED)[number],
  day: DayIndex,
): { story: StorySummary; presence: number } | null {
  if (day < parsed.start) return null
  const last7 = sum(parsed, day - 6, day)
  const lo = 0.5 * parsed.fadedBelow
  if (last7 < lo) return null
  const presence = smoothstep(lo, 3 * lo, last7)
  const prev7 = sum(parsed, day - 13, day - 7)
  const e = parsed.event
  const places = [e.place, ...(e.also ?? [])]
  // The main place gets half the attention; the others share the rest.
  const weights = places.map((_, i) => (i === 0 ? 1 : 1 / (places.length - 1)))
  const total = weights.reduce((a, b) => a + b, 0)
  const story: StorySummary = {
    id: e.id,
    title: e.title,
    location: location(e.place),
    category: e.category,
    footprint: places.map((pl, i) => ({
      lat: pl.lat,
      lon: pl.lon,
      weight: weights[i] / total,
      radius_km: e.radiusKm ?? DEFAULT_RADIUS_KM,
    })),
    article_count_7d: last7,
    post_count_7d: 0,
    growth_7d: Math.max(-6, Math.min(6, Math.log2((last7 + 1) / (prev7 + 1)))),
    tone: TONE[e.category],
    first_seen: toDay(parsed.start),
    forecast: null,
    outcome: null,
  }
  return { story, presence }
}

/**
 * Every archive story that was in the news on `day`, and each one's presence (0..1), which the
 * globe uses to grow a story's marker in as it builds and shrink it as it fades.
 */
export function historySnapshot(day: DayIndex): {
  snapshot: Snapshot
  presence: ReadonlyMap<string, number>
} {
  const stories: StorySummary[] = []
  const presence = new Map<string, number>()
  for (const parsed of PARSED) {
    const s = summary(parsed, day)
    if (!s) continue
    stories.push(s.story)
    presence.set(s.story.id, s.presence)
  }
  return { snapshot: { as_of: toDay(day), stories }, presence }
}

export const isHistoryStory = (id: string) => BY_ID.has(id)

/** An archive story's detail: no made-up headlines, just a link to the event on Wikipedia. */
export function historyDetail(id: string): StoryDetail {
  const parsed = BY_ID.get(id)
  if (!parsed) throw new Error(`No archive story ${id}`)
  const e = parsed.event
  const places = [e.place, ...(e.also ?? [])].map(location)
  const wikipedia: Item = {
    id: `${id}-wikipedia`,
    kind: 'wikipedia',
    title: `Wikipedia: ${e.wiki}`,
    url: `https://en.wikipedia.org/wiki/Special:Search?go=Go&search=${encodeURIComponent(e.wiki)}`,
    published_at: `${toDay(parsed.start)}T00:00:00Z`,
    source: 'en.wikipedia.org',
    language: 'en',
    similarity: 1,
    locations: places,
  }
  return {
    id,
    title: e.title,
    summary: SUMMARY,
    first_seen: toDay(parsed.start),
    locations: places,
    entities: [],
    timeline: [],
    items: [wikipedia],
  }
}
