"""SYNTHETIC: the frozen V1 algorithm remains executable on unknown heat."""
from copy import deepcopy
from recommendation_eval.adapters import BaselineV1Adapter


def test_unknown_heat_is_adapter_numeric_default_not_a_changed_snapshot():
    movies = [dict(movie_id=910001,title='SYNTHETIC unknown heat',catalog_status='active',
                   genres=['剧情'],release_year=2000,original_language='zh',catalog_popularity=None)]
    before = deepcopy(movies)
    for mode in ('raw', 'prefiltered'):
        result = BaselineV1Adapter(mode=mode).rank(movies, {}, {'hard_conditions': {}})
        assert [m['movie_id'] for m in result['items']] == [910001]
        assert result['items'][0]['components']['baseline'] == 0
        assert movies == before and movies[0]['catalog_popularity'] is None
