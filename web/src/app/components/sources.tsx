import { Skeleton } from "@/app/components/skeleton";
import { Wrapper } from "@/app/components/wrapper";
import { Source } from "@/app/interfaces/source";
import { BookText } from "lucide-react";
import { FC } from "react";

const SourceItem: FC<{ source: Source; index: number }> = ({
  source,
  index,
}) => {
  const { id, snippet, url } = source;
  const domain = new URL(url).hostname;
  return (
    <div
      className="relative text-xs py-3 px-3 bg-zinc-100 hover:bg-zinc-200 rounded-lg flex flex-col gap-2"
      key={id}
    >
      <a href={url} target="_blank" className="absolute inset-0"></a>
      <div className="font-medium text-zinc-950 text-ellipsis text-wrap">
        {index+1}. {snippet}
      </div>
    </div>
  );
};

export const Sources: FC<{ sources: Source[] }> = ({ sources }) => {
  return (
    <Wrapper
      title={
        <>
          <BookText></BookText> Sources
        </>
      }
      content={
        <div className="grid grid-cols-2 sm:grid-cols-1 gap-2">
          {sources.length > 0 ? (
            sources.map((item, index) => (
              <SourceItem
                key={item.id}
                index={index}
                source={item}
              ></SourceItem>
            ))
          ) : (
            <>
              <Skeleton className="max-w-sm h-16 bg-zinc-200/80"></Skeleton>
              <Skeleton className="max-w-sm h-16 bg-zinc-200/80"></Skeleton>
              <Skeleton className="max-w-sm h-16 bg-zinc-200/80"></Skeleton>
              <Skeleton className="max-w-sm h-16 bg-zinc-200/80"></Skeleton>
            </>
          )}
        </div>
      }
    ></Wrapper>
  );
};
