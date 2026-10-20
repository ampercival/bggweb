"""One column contract for table headings, sorting, and CSV export."""
COLUMNS = [
    ('score_factor', 'Score', 'score_factor'), ('title', 'Game', 'title'),
    ('game_id', 'Game ID', 'game_id'), ('year', 'Year', 'year'),
    ('bgg_rank', 'BGG Rank', 'bgg_rank'), ('avg_rating', 'Avg rating', 'avg_rating'),
    ('num_voters', 'Voters', 'num_voters'), ('weight', 'Weight', 'weight'),
    ('weight_votes', 'Weight votes', 'weight_votes'), ('owners', 'Availability', 'owned_by_str'),
    ('type', 'Type', 'type'), ('families', 'Families', 'families_str'),
    ('categories', 'Categories', 'categories_str'), ('mechanics', 'Mechanics', 'mechanics_str'),
    ('player_count', 'Players', 'player_count'), ('best_pct', 'Best %', 'best_pct'),
    ('best_votes', 'Best votes', 'best_votes'), ('rec_pct', 'Rec %', 'rec_pct'),
    ('rec_votes', 'Rec votes', 'rec_votes'), ('not_pct', 'Not %', 'not_pct'),
    ('not_votes', 'Not votes', 'not_votes'), ('total_votes', 'Poll votes', 'total_votes'),
    ('pc_score_unadj', 'Raw player score', 'pc_score_unadj'), ('pc_score', 'Player score', 'pc_score'),
    ('playable', 'Recommended', 'playable'),
]
SORTABLE = {key for key, _, _ in COLUMNS} - {'owners', 'families', 'categories', 'mechanics'}
