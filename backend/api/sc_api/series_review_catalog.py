"""Read-only staff catalog projection of the active transcript Editor store.

Private Beta ownership is not copied into production's manuscript/catalog. Each
series request reads current metadata and advertises only intact review drafts.
"""
from pathlib import Path
from urllib.parse import quote

from .transcript_review import ReviewStore,read,file_hash,ReviewError


class SeriesReviewCatalog:
    def __init__(self, production_root, beta_root=None):
        self.production=Path(production_root).resolve()
        self.beta=Path(beta_root).resolve() if beta_root else None
        self.catalogs={root:self._catalog(root) for root in {self.production,self.beta} if root}

    @staticmethod
    def _catalog(root):
        try:
            return {row['item']:row for row in read(root/'config/sermon.json',[]) if isinstance(row,dict) and row.get('item')}
        except (OSError,ValueError,TypeError):
            return {}

    def _draft(self,root,item):
        meta=self.catalogs.get(root,{}).get(item)
        if not meta:return None
        folder=ReviewStore(root).folder(item)
        context=read(folder/'context.json',{})
        if (not isinstance(context,dict) or context.get('schema_version')!='transcript-review-context.v1'
                or not context.get('source_video_sha256')
                or file_hash(root/'script_review'/f'{item}.json')!=context.get('script_sha256')):
            return None
        expected=self.catalogs.get(self.production,{}).get(item,{}).get('media_ingest_source_sha256')
        if expected and context['source_video_sha256']!=expected:return None
        return meta

    def _publication(self,item):
        # Publication and current draft ownership are independent. Legacy
        # unclaim operations may leave status=ready without withdrawing a release.
        path=self.production/'script_published'/f'{item}.json'
        if not path.is_file():return {}
        meta=self.catalogs.get(self.production,{}).get(item,{})
        snapshot=read(path,{})
        published=snapshot.get('metadata',{}) if isinstance(snapshot,dict) else {}
        if (meta.get('status')!='published' and published.get('status')!='published'
                and published.get('review_revision') is None):return {}
        date=meta.get('published_date') or published.get('published_date')
        revision=published.get('review_revision')
        if type(revision) is int and revision>=0:
            history=read(self.production/'transcript_review'/item/'revisions'/f'{revision:08}.json',{})
            if (history.get('kind')=='publish' and history.get('revision')==revision
                    and history.get('operation_id')==published.get('review_operation_id')):
                date=history.get('time') or date
        return dict(status='published',published_date=date)

    def for_item(self,item):
        try:
            # Validate once before using the identifier in any file path.
            ReviewStore(self.production).folder(item)
            publication=self._publication(item)
            root=self.production;href='/admin/surmons/'+quote(item,safe='')
            marker=read(ReviewStore(self.beta).folder(item)/'production-link.json') if self.beta else None
            if marker is not None:
                receipt=self.production/'transcript_review'/item/'beta-promotion.json'
                promotion=read(receipt,{})
                if (not isinstance(marker,dict) or marker.get('schema_version')!='transcript-production-link.v1' or marker.get('item')!=item
                        or marker.get('production_root')!=str(self.production)
                        or file_hash(receipt)!=marker.get('receipt_sha256')
                        or not isinstance(promotion,dict) or promotion.get('item')!=item):return {}
                meta=self._draft(self.production,item)
                href='/admin/transcript-beta/'+quote(item,safe='')
            else:
                meta=self._draft(self.production,item)
                if meta is None and self.beta:
                    root=self.beta;meta=self._draft(root,item)
                    href='/admin/transcript-beta/'+quote(item,safe='')
            if meta is None:
                if not publication:return {}
                legacy=self.catalogs.get(self.production,{}).get(item,{})
                return dict(**publication,assigned_to=legacy.get('assigned_to'),assigned_to_name=legacy.get('assigned_to_name'),
                            assigned_to_date=legacy.get('assigned_to_date'))
            return dict(status='published' if publication else 'assigned' if meta.get('assigned_to') else 'ready',
                        editor_href=href,assigned_to=meta.get('assigned_to'),assigned_to_name=meta.get('assigned_to_name'),
                        assigned_to_date=meta.get('assigned_to_date'),published_date=publication.get('published_date'))
        except (OSError,ValueError,TypeError,KeyError,ReviewError):
            return {}
